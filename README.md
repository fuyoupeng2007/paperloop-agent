# PaperLoop

PaperLoop 是本机使用的论文阅读应用。它解析 PDF、翻译原文、支持原页与译文对照、图表和段落交互、批注、论文背景查证、跨论文比较，以及有步数上限和证据核查的研究 Agent Loop。

Windows 安装版可从 [公开下载页](https://github.com/fuyoupeng2007/paperloop-download/releases/latest) 获取，使用方法见 [安装说明](outputs/paper-reader/INSTALL.md)：安装后填入自己的 DeepSeek API 密钥，无需部署 Python、Node 或 Codex。模型生成需要网络；API 模式尚未接独立联网搜索工具。构建入口为 `outputs/paper-reader/Build-Installer.ps1`。

应用源码、桌面入口、依赖、测试和运行说明位于 [outputs/paper-reader/README.md](outputs/paper-reader/README.md)。设计方案和调研材料位于 `outputs/` 和 `work/`；`work/` 中的可复用检查脚本也随仓库保存。

运行时的用户 PDF、数据库、ChatGPT 安装及登录路径、缓存、截图、测试临时数据和构建产物仅保存在本机，没有纳入仓库。克隆后需要按应用 README 安装依赖，再导入自己的论文并连接自己的模型账号。
