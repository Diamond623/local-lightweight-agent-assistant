#!/usr/bin/env python3
"""Update an existing Aurora installation, backing up code only."""
import argparse
import datetime
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import native_build

FILES = (
    'assets/cosmic-field.png',
    'projects.py', 'test_projects.py', 'test_projects_ui.cjs',
    'resource_monitor.py', 'test_resource_monitor.py', 'test_resources_ui.cjs',
    'system_info.py', 'test_system_info.py',
    'data_paths.py', 'test_state_persistence.py',
    'desktop_control.py', 'DesktopControl.swift', 'AuroraDesktop', 'test_desktop_control.py',
    'agent.py', 'test_agent.py', 'test_agent_ui.cjs',
    'test_local_model.py', 'test_navigation_ui.cjs', 'test_qwen27b.py', 'test_qwen27b_ui.cjs',
    'cleanup_unused_models.py', 'Clean-Unused-Models.command', 'test_cleanup_unused_models.py',
    'auto_memory.py', 'memory_worker.py', 'test_auto_memory.py',
    'native_build.py', 'test_native_build.py',
    'account-service.example.json', 'account_client.py', 'account_server.py', 'admin.html', 'test_account_server.py', 'Start-Account-Service.command',
    'auth.py', 'login.html', 'test_auth.py',
    'welcome.py', 'test_welcome.py',
    'scheduled_tasks.py', 'daily_briefing.py', 'install_briefing.py', 'Install-Daily-Briefing.command', 'test_briefing.py',
    'app.py', 'conversation.py', 'generation.py', 'memory_engine.py', 'local_clock.py', 'test_local_clock.py',
    'websearch.py', 'search_plan.py', 'weather.py', 'documents.py', 'index.html',
    'README.md', 'install_update.py', 'Apply-Update.command',
    'test_logic.py', 'test_documents.py', 'test_download.py', 'test_weather.py',
    'test_websearch.py', 'test_conversation.py', 'test_generation.py',
    'test_update.py', 'test_attachments.cjs', 'test_chat_ui.cjs', 'test_ui_server.py',
    'test_chat_integration.cjs', 'run_ui_integration.py',
    'Desktop.swift', 'Aurora.app/Contents/MacOS/Aurora',
    'Aurora.app/Contents/_CodeSignature/CodeResources',
    'Aurora.app/Contents/Info.plist',
    'Aurora.app/Contents/Resources/Aurora.icns',
    'Aurora.app/Contents/Resources/Aurora-Icon.png',
)
DEFAULT_TARGET = Path.home() / 'Applications' / 'Aurora'


def backup_file(backup, name):
    # Backup resources must not register as another launchable Aurora app.
    return backup / name.replace('Aurora.app/', 'Aurora-bundle-backup/', 1)


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='.aurora-update-', dir=destination.parent)
    os.close(handle)
    staged = Path(name)
    try:
        shutil.copy2(source, staged)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def install(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    plist = target / 'Aurora.app/Contents/Info.plist'
    if not (target / 'app.py').is_file() or not plist.is_file():
        raise ValueError('未找到原 Aurora 程序目录，请用 --target 指定包含 app.py 和 Aurora.app 的目录。')
    with plist.open('rb') as handle:
        if plistlib.load(handle).get('CFBundleIdentifier') != 'local.aurora.assistant':
            raise ValueError('目标目录不是原 Aurora 程序，已停止更新。')
    source_plist=source/'Aurora.app/Contents/Info.plist'
    if not source_plist.is_file():raise ValueError('更新包缺少 Aurora 应用配置。')
    with source_plist.open('rb') as handle:
        if plistlib.load(handle).get('CFBundleIdentifier')!='local.aurora.assistant':
            raise ValueError('更新包的 Aurora 应用配置不匹配。')
    for name in FILES:
        path = source / name
        if not path.is_file():
            raise ValueError('更新包不完整，缺少：' + name)
        if (target / name).is_symlink():
            raise ValueError('目标代码文件是符号链接，请先检查：' + name)
        if not (target/name).resolve().is_relative_to(target):
            raise ValueError('目标文件指向程序目录以外，已停止更新：'+name)
        if path.suffix == '.py':
            compile(path.read_text(encoding='utf-8'), name, 'exec')
    if source == target:
        return None
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = target / 'code-backups' / stamp
    backup.mkdir(parents=True)
    existed = set()
    for name in FILES:
        destination = target / name
        if destination.exists():
            backup_file(backup,name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(destination, backup_file(backup,name))
            existed.add(name)
    changed = []
    try:
        for name in FILES:
            atomic_copy(source / name, target / name)
            changed.append(name)
    except Exception:
        for name in reversed(changed):
            if name in existed:
                shutil.copy2(backup_file(backup,name), target / name)
            else:
                (target / name).unlink(missing_ok=True)
        raise
    return backup


def refresh_icon(target):
    bundle=Path(target).resolve()/'Aurora.app'
    if bundle.is_dir():os.utime(bundle,None)
    register=Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
    if sys.platform=='darwin' and register.is_file():
        try:subprocess.run([str(register),'-f',str(bundle)],capture_output=True,timeout=10,check=False)
        except (OSError,subprocess.TimeoutExpired):pass


def main():
    parser = argparse.ArgumentParser(description='只更新 Aurora 代码，不改动聊天、记忆、模型或桌面快捷方式。')
    parser.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()
    try:
        with native_build.prepare(Path(__file__).resolve().parent, FILES) as prepared:
            backup = install(prepared, args.target)
        refresh_icon(args.target)
    except Exception as error:
        print('更新失败：' + str(error))
        return 1
    if backup:
        print('Aurora-9.26 代码及图标更新完成。旧代码与图标备份：' + str(backup))
    else:
        print('当前目录已经是程序目录，无需复制，未创建额外代码备份。')
    print('请完全退出旧 Aurora 窗口，再从原桌面快捷方式打开。')
    print('聊天、Aurora Memory、模型权重和原快捷方式均保留。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
