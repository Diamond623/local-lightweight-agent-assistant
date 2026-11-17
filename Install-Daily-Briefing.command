#!/bin/zsh
cd -- "${0:A:h}" || exit 1
/opt/homebrew/bin/python3 install_briefing.py
if (( $? != 0 )); then
    print "定时任务未安装成功，请保留上方提示。"
fi
read "reply?按回车关闭…"
