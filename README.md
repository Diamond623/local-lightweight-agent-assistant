# Aurora

Aurora 是运行在 Apple Silicon Mac 上的本地 AI 助手。界面由 Swift / WebKit 与本地 Python 服务组成；模型推理由本机 `llama-server` 提供。支持模型切换、文档和图片附件、按需联网、账号与会话管理、长期记忆和需用户确认的项目工具。模型权重、用户聊天与账号数据不在仓库中。

## 运行环境

- macOS（Apple Silicon）；Xcode Command Line Tools（含 `swiftc`、`codesign`）；Homebrew Python 3 和 `llama-server`。部分工具调用固定使用 `/opt/homebrew/bin`，因此当前版本针对 Apple Silicon Homebrew 安装设计。
- Python 服务主要使用标准库。若需下载模型，先检查本地磁盘容量及应用中的模型文件路径；仓库**不包含模型**，首次运行不保证模型已就绪。
- 如使用桌面原生窗口，请先构建本机组件；仅查看页面与测试 Python 逻辑不需要安装模型权重。

```sh
git clone <你的仓库地址> Aurora
cd Aurora
python3 scripts/build_native.py
./Start.command
```

构建成功后也可以双击 `Aurora.app` 启动。启动后在界面中选择本机已有的模型；模型查找与兼容服务器的端口配置见 `app.py`。如果要在另一台 Mac 上运行，请自行安装 `llama-server` 并下载相应的 GGUF 权重。`Start.command` 仅启动程序，后台任务需由用户自行启用。

## 测试与打包

```sh
python3 -m unittest discover -p 'test_*.py'
node test_chat_ui.cjs
node test_attachments.cjs
python3 run_ui_integration.py
python3 scripts/build_native.py
python3 package_macos.py --zip-only
```

最后一步在 `dist/` 生成**不含模型与用户数据**的 macOS 安装 ZIP；如需 DMG，去掉 `--zip-only`。原开发记录保存在 `docs/history.md`。本项目目前只支持 macOS，未提供 Windows 安装包或自动更新发布流程。

## 本地数据与安全

默认个人资料位于 `~/Library/Application Support/Aurora`；如当前机器另行设置了桌面 `Aurora Memory`，它仍属于个人数据。不要把 `data/`、`models/`、`tasks-data/`、本地账号配置、日志或模型权重复制进仓库。`.gitignore` 是防误提交措施，**已经被 Git 跟踪的文件仍须单独检查**；提交前执行 `git status --short` 和 `git ls-files`。

统一账号功能涉及独立服务和部署配置，`account-service.example.json` 仅是示例；公开服务必须自行配置 HTTPS 与访问控制。Agent 项目工具可读写获授权的工作区，任何来自互联网的提示或代码都应人工审阅后执行。

## 代码使用与版权

项目代码采用 [MIT 许可证](LICENSE)。使用或再发布前，请另行核对图片、图标、模型及第三方素材的授权；MIT 许可证只适用于有权由本项目授权的内容。

## 稀疏模型：Qwen3.5 35B-A3B

24GB Apple Silicon Mac 可选用 Q3_K_M 量化版。运行 `Download-Sparse-Model.command` 下载并校验约 16.8GB 权重；中断后可再次运行续传。完成后重启 Aurora，在模型菜单选择“稀疏模式”。该模式仅处理文字，使用 4096 上下文。权重保存在本机 `models/`，不会上传到 GitHub。

来源：[Unsloth 的 Qwen3.5 35B-A3B GGUF](https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF)。
