"""Resume and verify Aurora's pinned local sparse model download."""
import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'models/qwen3.5-35b-a3b/Qwen3.5-35B-A3B-Q3_K_M.gguf'
PART = DEST.with_suffix('.gguf.partial')
SIZE = 16803691104
SHA256 = '3f26f8cca08e7142762b06c7e2aaa4f15eda0f6b963e094cb6434b129ed755ed'
URL = ('https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/'
       '01309d6e117f0506554cb84fa23185d0253ad242/'
       'Qwen3.5-35B-A3B-Q3_K_M.gguf?download=true')


def verified(path):
    if not path.is_file() or path.stat().st_size != SIZE:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == SHA256


def main():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if verified(DEST):
        print('稀疏模型已安装并通过校验。')
        return
    print('下载约 16.8GB；中断后可再次运行此脚本续传。', flush=True)
    subprocess.run(['curl', '-fL', '-C', '-', '--retry', '5', '--retry-delay', '5',
                    URL, '-o', str(PART)], check=True)
    if not verified(PART):
        raise RuntimeError('模型文件大小或 SHA-256 校验失败；未启用此文件。')
    PART.replace(DEST)
    print('下载完成并通过校验。重启 Aurora 后选择“稀疏模式”。')


if __name__ == '__main__':
    main()
