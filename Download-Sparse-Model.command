#!/bin/zsh
cd "${0:A:h}" || exit 1
/usr/bin/python3 download_sparse_model.py
echo "按回车键关闭窗口"
read
