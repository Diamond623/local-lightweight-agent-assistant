#!/bin/zsh
cd -- "${0:A:h}" || exit 1
/opt/homebrew/bin/python3 -u download_model.py
if (( $? != 0 )); then
    print "下载尚未完成，重新打开本入口可继续下载。"
fi
read "reply?按回车退出…"
