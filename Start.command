#!/bin/zsh
cd -- "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [[ ! -x "Aurora.app/Contents/MacOS/Aurora" ]]; then
    print "正在准备Aurora桌面窗口（首次启动）…"
    mkdir -p Aurora.app/Contents/MacOS
    /usr/bin/swiftc -module-cache-path "$TMPDIR/Aurora-swift-cache" Desktop.swift -o Aurora.app/Contents/MacOS/Aurora -framework Cocoa -framework WebKit -framework CoreLocation
    if (( $? != 0 )); then
        print "桌面窗口编译失败，请保留上方错误信息。"
        read "reply?按回车退出…"
        exit 1
    fi
fi
python3 app.py
if (( $? != 0 )); then
    read "reply?启动失败，请截图上方错误后按回车退出…"
fi
