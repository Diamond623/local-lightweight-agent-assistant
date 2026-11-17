"""Read-only macOS resource samples. RSS is not physical footprint or GPU memory."""
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

LOCK = threading.Lock()
CACHE = {'at': 0, 'value': None, 'key': None}
LABELS = {'qwen35': '快速 · Qwen3.5 9B', 'gemma': '均衡 · Gemma 4 12B', 'qwen27b': '深度 · Qwen3.8 27B', 'text': '旧文字模型', 'vision': '旧视觉模型'}


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_processes(text):
    rows = {}
    for line in (text or '').splitlines():
        values = line.strip().split(None, 3)
        if len(values) != 4:
            continue
        try:
            pid, parent, rss = map(int, values[:3])
        except ValueError:
            continue
        if pid > 0 and rss >= 0:
            rows[pid] = {'pid': pid, 'parent': parent, 'rss_bytes': rss * 1024, 'path': values[3]}
    return rows


def parse_vm(text):
    fields = {
        'compressed_bytes': 'Pages occupied by compressor', 'free_pages_bytes': 'Pages free',
        'wired_bytes': 'Pages wired down', 'anonymous_bytes': 'Anonymous pages',
        'file_backed_bytes': 'File-backed pages',
    }
    result = dict.fromkeys(fields)
    match = re.search(r'page size of (\d+) bytes', text or '')
    if not match:
        return result
    size = int(match[1])
    for key, label in fields.items():
        match = re.search(re.escape(label) + r':\s*(\d+)', text)
        if match:
            result[key] = int(match[1]) * size
    return result


def parse_swap(text):
    values = {}
    for key, number, unit in re.findall(r'(total|used|free)\s*=\s*([\d.]+)([KMGT]?)', text or ''):
        values[key + '_bytes'] = int(float(number) * (1024 ** (' KMGT'.index(unit) if unit else 0)))
    return values


def sample(root_pid, native_binary, owned_models, ports):
    """Only attributed app descendants and listeners on known model ports are exposed."""
    if sys.platform != 'darwin':
        return {'available': False, 'message': '资源面板目前支持 macOS', 'processes': [], 'rss_sum_bytes': None, 'system': {}}
    rows = parse_processes(command(['/bin/ps', '-axo', 'pid=,ppid=,rss=,comm=']))
    selected = {}
    if root_pid in rows:
        selected[root_pid] = ('后台服务', 'backend')
    descendants = {root_pid}
    for _ in range(len(rows)):
        added = {pid for pid, row in rows.items() if row['parent'] in descendants} - descendants
        if not added:
            break
        descendants.update(added)
    native = str(Path(native_binary))
    for pid in descendants:
        row = rows.get(pid)
        if row and row['path'] == native:
            selected[pid] = ('Aurora 窗口', 'window')
    for mode, pid in owned_models.items():
        if pid in rows:
            selected[pid] = (LABELS.get(mode, mode), 'model')
    # Services launched by another terminal are distinguished, never treated as owned.
    external = {}
    for mode, port in ports.items():
        found = command(['/usr/sbin/lsof', '-nP', '-a', '-iTCP:' + str(int(port)), '-sTCP:LISTEN', '-Fp'])
        for value in (found or '').splitlines():
            if value.startswith('p') and value[1:].isdigit():
                pid = int(value[1:]);row = rows.get(pid)
                if row and 'llama-server' in Path(row['path']).name and pid not in selected:
                    external[pid] = (LABELS.get(mode, mode), 'external_model')
    processes = []
    for pid, (label, kind) in {**selected, **external}.items():
        processes.append({'pid': pid, 'label': label, 'kind': kind, 'rss_bytes': rows[pid]['rss_bytes']})
    total = command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])
    pressure = command(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])
    pressure = {'1': 'normal', '2': 'warning', '4': 'critical'}.get((pressure or '').strip(), 'unknown')
    system = {**parse_vm(command(['/usr/bin/vm_stat'])),
              'total_bytes': int(total.strip()) if total and total.strip().isdigit() else None,
              'pressure': pressure, 'swap': parse_swap(command(['/usr/sbin/sysctl', 'vm.swapusage']))}
    parts = [system.get(key) for key in ('wired_bytes', 'anonymous_bytes', 'compressed_bytes')]
    used = sum(parts) if all(value is not None for value in parts) else None
    if used is not None and system['total_bytes'] is not None and used > system['total_bytes']:
        used = None
    system['used_estimate_bytes'] = used
    system['estimate_method'] = '已使用为匿名页 + 联动页 + 压缩器占用的估算；文件页单列，不等同于活动监视器的精确分类。'
    return {'available': bool(rows), 'sampled_at': time.time(), 'processes': processes,
            'rss_sum_bytes': sum(rows[pid]['rss_bytes'] for pid in selected) if root_pid in rows else None,
            'system': system, 'message': '' if rows else '系统暂不允许读取进程统计；其他指标仍可单独显示。',
            'scope': '本次 Aurora 后台、可识别窗口及其管理的模型；独立启动的模型单列，不计入合计。',
            'method': 'RSS 常驻内存估算，合计可能重复统计共享页；不等于精确物理占用或独立显存。WebKit 辅助进程若无法可靠归属，不计入合计。'}


def snapshot(root_pid, native_binary, owned_models, ports):
    key = (root_pid, str(native_binary), tuple(sorted(owned_models.items())), tuple(sorted(ports.items())))
    with LOCK:
        now = time.monotonic()
        if CACHE['key'] == key and CACHE['value'] is not None and now - CACHE['at'] < 0.5:
            return CACHE['value']
        value = sample(root_pid, native_binary, owned_models, ports)
        CACHE.update(at=time.monotonic(), value=value, key=key)
        return value
