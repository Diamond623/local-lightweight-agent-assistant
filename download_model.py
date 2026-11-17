"""Pinned, resumable GGUF download; verify SHA-256 before marking complete."""
import hashlib
import fcntl
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parent / 'models' / 'gemma4-12b'
REVISION = 'fc034cfff751157913579611efad8462ac1be606'
FILES = [
    ('gemma-4-12b-it-Q5_K_M.gguf', 8413576000,
     '32c554c7d1338a23837ae39b3db482213a4a642f96df3b2619f516a2375d16cd'),
    ('mmproj-F16.gguf', 175115840,
     '91f086971e56d7a7d8d39e271873fccdb49541bd259d6e02c401a4f1cb7a219e'),
]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(name, size, sha):
    ROOT.mkdir(parents=True, exist_ok=True)
    dest = ROOT / name
    part = dest.with_suffix('.gguf.partial')
    if dest.exists():
        if dest.stat().st_size == size and digest(dest) == sha:
            print(f'已验证 {name}', flush=True)
            return
        raise RuntimeError(f'现有文件校验失败，请检查后移走：{dest}')
    url = f'https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/resolve/{REVISION}/{name}?download=true'
    for attempt in range(5):
        try:
            start = part.stat().st_size if part.exists() else 0
            if start > size:
                raise RuntimeError('下载临时文件超出预期大小')
            if start == size:
                break
            headers = {'User-Agent': 'AuroraLocal/1.0'}
            if start:
                headers['Range'] = f'bytes={start}-'
            print(f'下载 {name}，从 {start / 1e9:.2f} / {size / 1e9:.2f} GB 继续', flush=True)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                if start and response.status == 200:
                    start = 0  # Server ignored Range: replace partial instead of appending.
                elif start and not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                    raise RuntimeError('服务器续传位置不匹配')
                total, reported = start, time.monotonic()
                with part.open('ab' if start else 'wb') as out:
                    while True:
                        block = response.read(4 * 1024 * 1024)
                        if not block:
                            break
                        out.write(block)
                        total += len(block)
                        if total > size:
                            raise RuntimeError('服务器返回的数据超过预期大小')
                        if time.monotonic() - reported > 5:
                            print(f'{name}: {total / size:.1%} ({total / 1e9:.2f} GB)', flush=True)
                            reported = time.monotonic()
            if part.stat().st_size != size:
                raise RuntimeError('文件未下载完整，保留进度以便续传')
            break
        except Exception as e:
            print(f'第 {attempt + 1} 次下载未完成：{e}', flush=True)
            if attempt == 4:
                raise
            time.sleep(3)
    print(f'校验 {name}…', flush=True)
    if part.stat().st_size != size or digest(part) != sha:
        raise RuntimeError(f'SHA-256 校验失败，未安装：{part}')
    part.rename(dest)
    print(f'完成 {name}', flush=True)


if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'download.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('下载已在进行中，请不要重复启动。')
        for item in FILES:
            download(*item)
        print('模型与视觉文件均已完成校验，可以在 Aurora 中启动。', flush=True)
