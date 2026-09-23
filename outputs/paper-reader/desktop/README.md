# PaperLoop Windows 桌面版

双击上一层的 `Start-PaperLoop-Desktop.cmd`，或双击 `dist/PaperLoop.Desktop.exe`。会打开独立桌面窗口，无须手动开启浏览器或命令窗口。

- 应用根据可执行文件所在位置查找完整项目，从任何工作目录启动都可以。
- 已有本机论文服务时直接复用；服务尚未启动时，使用项目 `.venv` 隐藏启动 `127.0.0.1:8765`。
- 关闭窗口后，已开始的论文处理继续在后台运行。再次打开后会恢复到同一个本地文献库。
- 下载导出文件时会弹出 Windows“另存为”。应用内的外部网页链接通过默认浏览器打开。
- 浏览器缓存保存在项目 `data/webview2/`，后台日志保存在 `data/desktop-backend.log`。桌面入口不读取、复制或修改 ChatGPT 凭据；登录由应用的连接流程处理。

本机已具备运行所需的 .NET 10 Desktop Runtime、Microsoft Edge WebView2 Runtime 和 Python 环境。当前交付针对这台电脑，移动到其他电脑时需要一同部署这些运行环境和完整项目；只复制 EXE 无法运行。

## 重新构建

安装 .NET 10 SDK 后，在项目根目录运行：

```powershell
.\desktop\Build-Desktop.ps1
```

NuGet 包和构建缓存保存在 `desktop/.packages` 与 `desktop/.build-home`，发布产物保存在 `desktop/dist`。无需 Node.js 参与桌面壳构建；界面资源仍使用项目根目录的 `dist`。

实现依据：[微软 WinForms WebView2 文档](https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/winforms)。

## 应用图标

`assets/paperloop.svg` 是原创矢量源：深绿封面、奶油色书页、书签与暖金阅读回环。`assets/paperloop.ico` 包含 16、24、32、40、48、64、96、128、256 像素九档图标，嵌入 EXE 并用于窗口标题栏。

修改 SVG 后，可在项目根目录运行 `node desktop/Render-Icon.mjs`，再运行 `.venv/Scripts/python.exe desktop/Build-Icon.py` 重新生成 PNG 和 ICO；使用项目已有的 Playwright 与 Pillow。

为避免覆盖运行中的程序，先执行 `desktop/Build-Desktop.ps1 -OutputDirectory ./desktop/staged` 验证新版本。关闭已有 PaperLoop **桌面窗口**后，再执行通常的 `desktop/Build-Desktop.ps1` 更新 `desktop/dist`。后台论文处理不会因关闭桌面窗口而中断。

执行 `desktop/Update-DesktopShortcut.ps1` 可创建或更新当前用户桌面的 `PaperLoop.lnk`，直接启动桌面程序并使用新图标。也可通过 `-ShortcutPath` 指定已有快捷方式；脚本只写入这个明确的 `.lnk` 文件。
