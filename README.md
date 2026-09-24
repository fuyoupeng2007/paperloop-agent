# PaperLoop

PaperLoop 是 Windows 本地论文阅读与研究应用。它支持英文 PDF 解析和翻译、原文与译文对照、段落及图表交互、批注、论文背景查证、跨论文比较，以及有步数上限和证据核查的研究 Agent Loop。

## 下载与演示

从 [最新 Release](https://github.com/fuyoupeng2007/paperloop-agent/releases/latest) 下载 `PaperLoop-Setup-0.3.0-win-x64.exe`。安装版包含桌面运行环境、PDF 解析和 OCR，无需另装 Python、Node 或 Codex。首次打开后填入自己的 DeepSeek API 密钥并测试保存；模型生成需要联网并使用自己的 API 额度。

[观看 40 秒操作演示](https://github.com/fuyoupeng2007/paperloop-agent/releases/download/v0.3.0/PaperLoop-0.3.0-Demo.mp4)。演示使用虚构示例 PDF 和本地模拟模型；同一 Release 提供示例 PDF 与 SHA256 校验文件。

完整的安装步骤、数据保存方式和当前适用范围见 [Windows 安装说明](INSTALL.md)。

## 源码

应用源码、桌面入口、依赖、测试和开发运行说明位于 [outputs/paper-reader](outputs/paper-reader)。构建安装包的入口是 [Build-Installer.ps1](outputs/paper-reader/Build-Installer.ps1)。设计方案与调研材料位于 `outputs/`，开发和验收脚本位于 `work/`。

运行时的用户 PDF、数据库、模型密钥、账号登录信息、缓存、截图、测试临时数据和构建产物只保存在本机，不纳入仓库。克隆源码后需要按应用说明安装开发依赖，再导入自己的论文并连接自己的模型服务。

## 当前连接范围

安装版支持兼容 Chat Completions 的 API，并预填 DeepSeek 连接参数。API 模式目前没有独立联网搜索工具；真实 DeepSeek 密钥的端到端验收尚未完成。源码开发版可使用本机已有的 ChatGPT Codex 登录执行翻译、问答和联网查证。
