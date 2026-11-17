#!/bin/zsh
cd -- "${0:A:h}" || exit 1
/opt/homebrew/bin/python3 cleanup_unused_models.py --apply
if (( $? != 0 )); then
    read "reply?请保留上方错误信息；按回车退出…"
else
    read "reply?清理完成，按回车退出…"
fi
