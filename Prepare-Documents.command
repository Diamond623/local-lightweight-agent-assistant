#!/bin/zsh
cd -- "${0:A:h}" || exit 1
/usr/bin/swiftc -module-cache-path "$TMPDIR/aurora-pdf-cache" PDFRead.swift -o PDFRead -framework PDFKit -framework AppKit
if (( $? == 0 )); then
    print "PDF 文档工具已准备好。"
else
    print "编译失败，请保留上方错误。"
fi
read "reply?按回车退出…"
