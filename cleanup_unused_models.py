"""Remove only the retired models, retaining every shared/active model layer."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

RETIRED = {'registry.ollama.ai/library/qwen3/8b', 'registry.ollama.ai/library/qwen/latest'}
PROTECTED = {
    'sha256:02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d',
    'sha256:f836f08f921193f4d6b6a6952dba0a6fb116759d26a757bcb395d570d73976ca',
    'sha256:40fac4050e940397dbf13087afd50f4734a11805bf9d65ef8ddd7483470e6199',
    'sha256:83ee4f4f205fa514161778c41df1ea14144faa0f713510893b63c2395f5c2d53',
}


def clean(apply=False):
    root = Path.home()/'.ollama/models'
    manifests = root/'manifests'
    retired, kept, snapshots = set(), set(), {}
    for path in manifests.rglob('*'):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        data = json.loads(raw)
        layers = {row['digest'] for row in [data['config']]+data['layers']}
        if path.relative_to(manifests).as_posix() in RETIRED:
            retired.update(layers)
            snapshots[path] = raw
        else:
            kept.update(layers)
    digests = retired-kept-PROTECTED
    assert all(len(d)==71 and d.startswith('sha256:') and all(c in '0123456789abcdef' for c in d[7:]) for d in digests)
    blobs = [root/'blobs'/d.replace(':','-') for d in sorted(digests)]
    blobs = [p for p in blobs if p.is_file()]
    cache = Path.home()/'.cache/huggingface/hub/models--Qwen--Qwen3-VL-4B-Instruct-GGUF'
    cache_files = [p for p in cache.rglob('*') if p.is_file() and not p.is_symlink()]
    size = sum(p.stat().st_size for p in blobs+cache_files)
    print(f'待清理：旧 Qwen3 8B、qwen:latest、Qwen3-VL 4B 缓存；约 {size/1e9:.2f} GB。', flush=True)
    if not apply:
        print('预览完成，未删除。')
        return
    if not all((root/'blobs'/d.replace(':','-')).is_file() for d in PROTECTED):
        raise RuntimeError('保留模型的组件不齐全，已停止清理，请先核查。')
    if cache.is_symlink():
        raise RuntimeError('缓存目录为符号链接，未开始删除。')
    files = blobs+cache_files
    if files:
        usage = subprocess.run(['/usr/sbin/lsof','-t',*map(str,files)],capture_output=True,text=True)
        if usage.stdout.strip():
            raise RuntimeError('有程序正在使用待清理模型，请先退出该程序。未开始删除。')
        if usage.returncode not in (0,1):
            raise RuntimeError('无法检查模型是否正在使用，未开始删除。')
    for path,raw in snapshots.items():
        if path.read_bytes()!=raw:
            raise RuntimeError('模型清单发生变化，未开始删除，请重新运行。')
    removed = 0
    for path in blobs:
        path.unlink()
        removed += 1
    for path in snapshots:
        path.unlink()
    if cache.exists():
        shutil.rmtree(cache)
    assert all((root/'blobs'/d.replace(':','-')).is_file() for d in PROTECTED)
    print(f'清理完成，移除 {removed} 个旧模型层及视觉缓存，释放约 {size/1e9:.2f} GB。')
    print('已保留 Qwen3.5 9B、Gemma 4 12B、Qwen3.8 27B 及配套组件。')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        clean(args.apply)
    except (PermissionError,OSError,RuntimeError) as error:
        print('清理未完成：'+str(error),file=sys.stderr)
        sys.exit(1)
