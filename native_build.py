"""Compile and sign an updated native Mac window before touching the installation."""
from contextlib import contextmanager
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile


def command(args,timeout=180):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        detail=(result.stderr or result.stdout or '')[-1600:]
        raise RuntimeError('Mac 桌面组件构建失败，原安装未更新。请确认 Xcode 命令行工具可用。\n'+detail)


@contextmanager
def prepare(source,files,rebuild=None):
    source=Path(source).resolve()
    if not (sys.platform=='darwin' if rebuild is None else rebuild):
        yield source
        return
    with tempfile.TemporaryDirectory(prefix='Aurora-native-update-') as directory:
        staged=Path(directory)/'Aurora'
        for name in files:
            destination=staged/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/name,destination)
        bundle=staged/'Aurora.app'
        binary=bundle/'Contents/MacOS/Aurora'
        print('正在重建 Aurora Mac 桌面组件，更新导航与登录保存…',flush=True)
        command(['/usr/bin/xcrun','swiftc','-module-cache-path',str(Path(directory)/'swift-cache'),
                 str(staged/'Desktop.swift'),'-o',str(binary),'-framework','Cocoa','-framework','WebKit','-framework','CoreLocation'])
        binary.chmod(0o755)
        plist=bundle/'Contents/Info.plist'
        with plist.open('rb') as handle:values=plistlib.load(handle)
        values.update(CFBundleShortVersionString='9.26',CFBundleVersion='9.26')
        values['NSLocationWhenInUseUsageDescription']='仅在你点击定位时识别所在城市，用于天气查询；不保存坐标或持续跟踪。'
        values['NSLocationUsageDescription']=values['NSLocationWhenInUseUsageDescription']
        plist.write_bytes(plistlib.dumps(values))
        command(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',str(bundle)],60)
        command(['/usr/bin/codesign','--verify','--strict',str(bundle)],30)
        yield staged
