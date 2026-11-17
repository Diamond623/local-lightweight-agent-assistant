"""Build source-controlled Swift helpers for this Mac; outputs stay ignored by Git."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    print('Building:', args[0], flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Native Aurora builds require macOS.')
    swiftc = '/usr/bin/swiftc'
    bundle = ROOT / 'Aurora.app'
    binary = bundle / 'Contents/MacOS/Aurora'
    binary.parent.mkdir(parents=True, exist_ok=True)
    cache = str(ROOT / 'build/swift-module-cache')
    (ROOT / 'build/swift-module-cache').mkdir(parents=True, exist_ok=True)
    run(swiftc, '-module-cache-path', cache, 'Desktop.swift', '-o', str(binary),
        '-framework', 'Cocoa', '-framework', 'WebKit', '-framework', 'CoreLocation')
    run(swiftc, '-module-cache-path', cache, 'DesktopControl.swift', '-o', 'AuroraDesktop',
        '-framework', 'Cocoa')
    run(swiftc, '-module-cache-path', cache, 'PDFRead.swift', '-o', 'PDFRead',
        '-framework', 'PDFKit', '-framework', 'Foundation')
    binary.chmod(0o755)
    # Files copied from Finder can retain resource-fork/Finder attributes which
    # macOS codesign refuses to sign. Only clean this new build's app bundle.
    run('/usr/bin/xattr', '-cr', str(bundle))
    run('/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(bundle))
    print('Native components ready. Use ./Start.command or python3 package_macos.py --zip-only.')


if __name__ == '__main__':
    main()
