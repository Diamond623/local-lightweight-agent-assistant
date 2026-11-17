#!/usr/bin/env python3
"""Make a clean, model-free macOS distribution of the current Aurora source."""
import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME = (
    'app.py', 'account_client.py', 'agent.py', 'auth.py', 'auto_memory.py',
    'conversation.py', 'daily_briefing.py', 'data_paths.py', 'desktop_control.py',
    'documents.py', 'generation.py', 'memory_engine.py', 'memory_worker.py',
    'projects.py', 'resource_monitor.py', 'scheduled_tasks.py', 'search_plan.py',
    'weather.py', 'websearch.py', 'welcome.py', 'index.html', 'login.html',
    'admin.html', 'PDFRead', 'AuroraDesktop', 'Desktop.swift',
    'DesktopControl.swift', 'PDFRead.swift', 'Start.command',
    'Download-Model.command', 'download_model.py',
    'Prepare-Documents.command', 'Clean-Unused-Models.command',
    'cleanup_unused_models.py', 'Install-Daily-Briefing.command',
    'install_briefing.py',
)
GUIDE = '''Aurora · macOS 安装说明

1. 将完整的「Aurora」文件夹拖入“应用程序”或个人目录；请不要只拖动文件夹里的 Aurora.app。
2. 本软件适用于 Apple Silicon Mac。需要本机已安装 /opt/homebrew/bin/python3 和 /opt/homebrew/bin/llama-server；首次启动前请确保已安装 llama.cpp 并准备所选模型。
3. 双击 Aurora 文件夹中的 Aurora.app。若 macOS 阻止打开，请在系统设置 → 隐私与安全性检查来源。此构建为本地临时签名，未做 Apple 公证。
4. 文档与对话可正常使用；模型推理需与本机对应权重配套。压缩包不含模型，请按应用界面的模型提示准备；联网功能需可用网络。
5. 账号、对话和长期记忆默认保存在当前用户的 ~/Library/Application Support/Aurora；如果原设备已指定桌面 Aurora Memory，旧数据继续留在原位置。分发包不会携带任何原用户的数据。

不要把本文件夹中的 app.py 单独移到别处；Aurora.app 与代码、assets 必须同处一个 Aurora 文件夹。不同 Mac 的模型权重不随安装包转移。
'''


def build(output: Path, dmg: bool = True) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aurora-package-') as temporary:
        stage = Path(temporary) / 'Aurora'
        stage.mkdir()
        for name in RUNTIME:
            source = ROOT / name
            if not source.is_file() or source.is_symlink():
                raise ValueError(f'缺少必要程序文件：{name}')
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        for name in ('Aurora.app', 'assets'):
            shutil.copytree(ROOT / name, stage / name, symlinks=False,
                            ignore=shutil.ignore_patterns('.DS_Store', '__pycache__'))
        with (stage / 'Aurora.app/Contents/Info.plist').open('rb') as handle:
            assert plistlib.load(handle).get('CFBundleIdentifier') == 'local.aurora.assistant'
        (stage / '安装说明.txt').write_text(GUIDE, encoding='utf-8')
        for name in RUNTIME:
            if name.endswith('.py'):
                compile((stage / name).read_text(), name, 'exec')
        # Only this explicit allowlist gets copied: no account databases, logs,
        # local memory symlinks, model weights, keys, workspace files or backups.
        name = 'Aurora-macOS-arm64'
        archive = output / (name + '.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=8) as z:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    info = zipfile.ZipInfo(str(path.relative_to(stage.parent)))
                    info.external_attr = ((path.stat().st_mode & 0o777) | 0o100000) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(info, path.read_bytes())
        results = [archive]
        if dmg:
            image = output / (name + '.dmg')
            try:
                command = subprocess.run(['/usr/bin/hdiutil', 'create', '-ov', '-format', 'UDZO',
                                          '-volname', 'Aurora', '-srcfolder', str(stage), str(image)],
                                         capture_output=True, text=True, timeout=180)
                if command.returncode:
                    raise RuntimeError((command.stderr or command.stdout).strip())
                results.append(image)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                image.unlink(missing_ok=True)
                print('磁盘映像未生成，保留可用 ZIP：', str(error)[:400])
        for path in results:
            print(path, 'SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
        return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--zip-only', action='store_true')
    args = parser.parse_args()
    build(args.output, not args.zip_only)
