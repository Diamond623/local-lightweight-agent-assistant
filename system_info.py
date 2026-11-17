"""Bounded, read-only snapshots of the local Mac for explicitly requested topics.

No filesystem traversal, process listing, serial numbers, network addresses,
shell commands supplied by the user/model, or background collection.
"""

import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil

import resource_monitor


ALL = ('system', 'cpu', 'memory', 'disk', 'battery')
LABELS = {'system': '系统', 'cpu': '处理器', 'memory': '内存',
          'disk': '磁盘', 'battery': '电池', 'aurora': 'Aurora 进程内存'}
_NOUN = re.compile(r'电脑|本机|这台(?:Mac|机器|设备)|MacBook|macOS|处理器|芯片|CPU|内存|RAM|硬盘|磁盘|存储|电池|Aurora|系统版本|系统信息|设备信息|硬件信息|资源占用', re.I)
_DETAIL = re.compile(r'配置|硬件信息|电脑信息|设备信息|机器信息|本机信息|系统信息|运行状态', re.I)
_REQUEST = re.compile(r'多少|多大|多高|还剩|剩余|占用|使用率|容量|电量|配置|信息|版本|状态|数据|什么|哪个|几核|查看|看看|显示|读一下|读读|检查|查一下|情况|已用|用了|有没有|呢$', re.I)
_EXPLAIN = re.compile(r'如何|怎么|为什么|原理|适合|推荐|建议|能不能|能否|可不可以|可以吗|能跑|能运行|选择|对比|比较|优化|调优|写|代码|假如|如果|假设|教学|课程|网页|搜索', re.I)


def topics(question):
    """Only the current user turn can authorize a fixed set of local reads."""
    if isinstance(question, str): question = question.strip().rstrip('。？?！!，, ')
    if not isinstance(question, str) or len(question) > 300 or not _NOUN.search(question):
        return ()
    if _EXPLAIN.search(question) and not re.search(r'我(?:的|这)|本机|这台|Aurora|当前|现在|还剩|剩余', question, re.I):
        return ()
    if not _REQUEST.search(question) and not re.search(r'我(?:的|这)|本机|这台|当前|现在', question):
        return ()
    if _DETAIL.search(question) and re.search(r'电脑|本机|机器|设备|Mac|^系统信息$', question, re.I):
        return ALL
    selected = []
    for key, pattern in (
        ('system', r'系统版本|什么系统|操作系统|macOS|电脑型号|设备型号'),
        ('cpu', r'CPU|处理器|芯片|核数|几核'),
        ('memory', r'内存|RAM|交换空间|内存压力'),
        ('disk', r'硬盘|磁盘|存储|空间|容量'),
        ('battery', r'电池|电量|充电|续航'),
        ('aurora', r'Aurora.{0,12}(?:内存|占用)|(?:内存|占用).{0,12}Aurora'),
    ):
        if re.search(pattern, question, re.I):
            selected.append(key)
    if not selected and re.search(r'电脑|本机|这台Mac|MacBook|机器',question,re.I):
        return ALL
    return tuple(selected)


def direct_topics(question):
    """Short status questions can bypass the model; analytical tasks use evidence."""
    if isinstance(question, str): question = question.strip().rstrip('。？?！!，, ')
    found = topics(question)
    if not found or len(question) > 90 or _EXPLAIN.search(question):
        return ()
    return found if _REQUEST.search(question) else ()


def _command(args):
    return resource_monitor.command(args)


def _sysctl(name):
    value = _command(['/usr/sbin/sysctl', '-n', name])
    return value.strip() if value and value.strip() else None


def _gib(number):
    return f'{number / 1024**3:.1f} GB'


def _hardware_profile():
    """Optional hardware fallback; never expose the full profiler output."""
    raw = _command(['/usr/sbin/system_profiler', 'SPHardwareDataType', '-json', '-detailLevel', 'mini'])
    if not raw:
        return {}
    try:
        entry = json.loads(raw)['SPHardwareDataType'][0]
        chip = entry.get('chip_type')
        memory = re.fullmatch(r'([\d.]+)\s*(MB|GB|TB)', str(entry.get('physical_memory','')).strip(), re.I)
        bytes_total = int(float(memory[1]) * 1024 ** {'MB': 2, 'GB': 3, 'TB': 4}[memory[2].upper()]) if memory else None
        return {'chip': chip if isinstance(chip, str) and len(chip) < 100 else None,
                'memory_bytes': bytes_total}
    except (ValueError, KeyError, IndexError, TypeError, OverflowError):
        return {}


def collect(requested, app_resources=None, question=''):
    """Collect only allowlisted categories; never return identifiers or raw output."""
    wanted = tuple(key for key in dict.fromkeys(requested) if key in (*ALL, 'aurora'))
    result = {'sampled_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'values': {}, 'unavailable': []}
    values, unavailable = result['values'], result['unavailable']
    chip = _sysctl('machdep.cpu.brand_string') if 'cpu' in wanted else None
    total = _sysctl('hw.memsize') if 'memory' in wanted else None
    profile = _hardware_profile() if (('cpu' in wanted and not chip) or ('memory' in wanted and not total)) else {}
    if 'system' in wanted:
        version = _command(['/usr/bin/sw_vers', '-productVersion'])
        values['system'] = {'macOS': version.strip() if version and version.strip() else None,
                            'architecture': platform.machine() or None}
        if not values['system']['macOS']: unavailable.append('macOS 版本')
    if 'cpu' in wanted:
        brand = chip or profile.get('chip')
        cores = os.cpu_count()
        values['cpu'] = {'chip': brand, 'logical_cores': cores}
        if not brand: unavailable.append('芯片名称')
        if re.search(r'CPU.{0,8}(?:温度|频率)|(?:温度|频率).{0,8}CPU|芯片温度', question, re.I):
            values['cpu']['sensor_requested'] = True
        if re.search(r'CPU.{0,8}(?:占用|使用率|负载)|(?:占用|使用率|负载).{0,8}CPU', question, re.I):
            values['cpu']['usage_requested'] = True
            output = _command(['/usr/bin/top', '-l', '1', '-n', '0'])
            usage = re.search(r'CPU usage:\s*[\d.]+% user,\s*[\d.]+% sys,\s*([\d.]+)% idle', output or '')
            if usage: values['cpu']['usage_percent'] = round(100 - float(usage[1]), 1)
            else: unavailable.append('当前 CPU 使用率')
    if 'memory' in wanted:
        vm = resource_monitor.parse_vm(_command(['/usr/bin/vm_stat']))
        pressure = _sysctl('kern.memorystatus_vm_pressure_level')
        swap = resource_monitor.parse_swap(_command(['/usr/sbin/sysctl', 'vm.swapusage']))
        parts = [vm.get(key) for key in ('anonymous_bytes', 'wired_bytes', 'compressed_bytes')]
        used = sum(parts) if all(x is not None for x in parts) else None
        size = int(total) if total and total.isdigit() else profile.get('memory_bytes')
        if size is not None and used is not None and used > size: used = None
        values['memory'] = {'total_bytes': size, 'used_estimate_bytes': used,
                            'free_pages_bytes': vm.get('free_pages_bytes'),
                            'pressure': {'1':'正常','2':'偏高','4':'严重'}.get(pressure),
                            'swap_used_bytes': swap.get('used_bytes')}
        if size is None: unavailable.append('物理内存总量')
        if used is None: unavailable.append('已使用内存估算')
    if 'disk' in wanted:
        try:
            disk = shutil.disk_usage(Path.home())
            values['disk'] = {'total_bytes': disk.total, 'used_bytes': disk.used,
                              'free_bytes': disk.free}
        except OSError:
            unavailable.append('用户主目录所在磁盘卷空间')
    if 'battery' in wanted:
        raw = _command(['/usr/bin/pmset', '-g', 'batt'])
        match = re.search(r'\b(\d{1,3})%', raw or '')
        if match and int(match[1]) <= 100:
            values['battery'] = {'percentage': int(match[1]),
                                 'power': '电源适配器' if "'AC Power'" in raw else '电池' if "'Battery Power'" in raw else None,
                                 'charging': bool(re.search(r';\s*charging\s*;',raw,re.I))}
        else: unavailable.append('电池电量')
        if re.search(r'电池.{0,6}(?:健康|循环|寿命)',question):
            values.setdefault('battery', {})['health_requested'] = True
    if 'aurora' in wanted:
        sample = app_resources() if app_resources else None
        rss = sample.get('rss_sum_bytes') if isinstance(sample, dict) else None
        values['aurora'] = {'rss_sum_bytes': rss}
        if rss is None: unavailable.append('Aurora 进程内存')
    return result


def describe(sample, requested):
    values = sample['values']
    rows = [f"本机实时读取（{sample['sampled_at']}）："]
    if 'system' in requested:
        row = values.get('system', {})
        rows.append('系统：macOS ' + str(row.get('macOS') or '暂不可读') + '，架构 ' + str(row.get('architecture') or '暂不可读') + '。')
    if 'cpu' in requested:
        row = values.get('cpu', {})
        rows.append('处理器：' + str(row.get('chip') or '芯片名称暂不可读') + '；逻辑核心 ' + str(row.get('logical_cores') or '暂不可读') + '。')
        if row.get('usage_requested'):
            usage = row.get('usage_percent')
            rows.append('当前 CPU 使用率：' + (f'{usage:.1f}%（单次采样）。' if usage is not None else '暂不可读。'))
        if row.get('sensor_requested'):
            rows.append('CPU 温度或频率：本机没有可供此功能可靠读取的指标，不能推测。')
    if 'memory' in requested:
        row = values.get('memory', {})
        total = row.get('total_bytes'); used = row.get('used_estimate_bytes'); free = row.get('free_pages_bytes')
        rows.append('内存：物理总量 ' + (_gib(total) if total is not None else '暂不可读') +
                    '；已用估算 ' + (_gib(used) if used is not None else '暂不可读') +
                    '；空闲页 ' + (_gib(free) if free is not None else '暂不可读') +
                    '；内存压力 ' + str(row.get('pressure') or '暂不可读') + '。')
        rows.append('空闲页不等于可用内存；已用是匿名页、联动页和压缩内存的估算。')
    if 'disk' in requested:
        row = values.get('disk')
        rows.append('用户主目录所在磁盘卷：' +
                    (f"总量 {_gib(row['total_bytes'])}，已用 {_gib(row['used_bytes'])}，剩余 {_gib(row['free_bytes'])}。"
                     if row else '容量暂不可读。'))
    if 'battery' in requested:
        row = values.get('battery')
        rows.append('电池：' +
                    (f"{row['percentage']}%，当前使用{row['power'] or '未知供电方式'}" +
                     ('，正在充电。' if row['charging'] else '。') if row and 'percentage' in row else '电量暂不可读。'))
        if row and row.get('health_requested'):
            rows.append('电池健康度与循环次数：本功能尚不能可靠读取；请在 macOS 系统设置中查看。')
    if 'aurora' in requested:
        size = values.get('aurora', {}).get('rss_sum_bytes')
        rows.append('Aurora 进程 RSS 合计：' + (_gib(size) + '（共享页可能重复计入，不含无法归属的辅助进程）。' if size is not None else '暂不可读。'))
    return '\n'.join(rows)
