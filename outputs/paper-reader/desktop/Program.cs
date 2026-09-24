using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace PaperLoop.Desktop;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        string? smokeFile = args.Length == 2 && args[0] == "--smoke-test" ? Path.GetFullPath(args[1]) : null;
        bool distributed = File.Exists(Path.Combine(AppContext.BaseDirectory, "PaperLoop.distributed"));
        using var appMutex = distributed ? new Mutex(true, "PaperLoop.Desktop.Installed", out _) : null;
        if (distributed && !OwnsMutex(appMutex!))
        {
            const string detail = "PaperLoop 已经打开，请从任务栏切换到阅读窗口。升级或卸载前，请先退出 PaperLoop。";
            if (smokeFile is not null)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(smokeFile)!);
                File.WriteAllText(smokeFile, JsonSerializer.Serialize(new { ok = false, error = detail }));
            }
            else MessageBox.Show(detail, "PaperLoop", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        Application.Run(new ReaderWindow(smokeFile, distributed));
    }

    private static bool OwnsMutex(Mutex mutex)
    {
        try { return mutex.WaitOne(0); }
        catch (AbandonedMutexException) { return true; }
    }
}

internal sealed class ReaderWindow : Form
{
    private readonly bool distributed;
    private string appAddress = "http://127.0.0.1:8765/";
    private int appPort = 8765;
    private string dataDirectory = "";
    private static readonly string AppVersion = typeof(Program).Assembly.GetName().Version?.ToString(3) ?? "0.3.0";
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Label message = new()
    {
        Dock = DockStyle.Fill,
        Text = "正在打开 PaperLoop…",
        TextAlign = ContentAlignment.MiddleCenter,
        ForeColor = Color.FromArgb(28, 73, 61),
        Font = new Font("Microsoft YaHei UI", 14),
    };
    private readonly CancellationTokenSource lifetime = new();
    private readonly string? smokeFile;
    private bool smokeWritten;

    public ReaderWindow(string? smokeFile, bool distributed)
    {
        this.smokeFile = smokeFile;
        this.distributed = distributed;
        Text = "PaperLoop · 论文阅读";
        BackColor = Color.FromArgb(247, 248, 244);
        MinimumSize = new Size(1000, 700);
        ClientSize = new Size(1400, 900);
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? SystemIcons.Application;
        Controls.Add(browser);
        Controls.Add(message);
        Shown += async (_, _) => await OpenAsync();
        FormClosed += (_, _) =>
        {
            lifetime.Cancel();
            browser.Dispose();
            // Development workers stay running; installed workers follow the parent PID.
        };
    }

    private string FindAppRoot()
    {
        if (distributed) return Path.GetFullPath(AppContext.BaseDirectory);
        for (DirectoryInfo? folder = new(AppContext.BaseDirectory); folder is not null; folder = folder.Parent)
        {
            if (File.Exists(Path.Combine(folder.FullName, "backend", "app.py")) &&
                File.Exists(Path.Combine(folder.FullName, "dist", "index.html")))
                return folder.FullName;
        }
        throw new InvalidOperationException("找不到应用文件。请在完整的 PaperLoop 文件夹中启动桌面程序。");
    }

    private async Task OpenAsync()
    {
        try
        {
            string root = FindAppRoot();
            string? requestedPort = Environment.GetEnvironmentVariable("PAPERLOOP_PORT");
            appPort = distributed ? 8766 : 8765;
            if (!string.IsNullOrWhiteSpace(requestedPort) &&
                (!int.TryParse(requestedPort, out appPort) || appPort < 1024 || appPort > 65535))
                throw new InvalidOperationException("PAPERLOOP_PORT 必须是 1024 到 65535 之间的端口号。");
            appAddress = $"http://127.0.0.1:{appPort}/";
            string? requestedData = Environment.GetEnvironmentVariable("PAPERLOOP_DATA");
            dataDirectory = Path.GetFullPath(!string.IsNullOrWhiteSpace(requestedData) ? requestedData : distributed
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PaperLoop", "data")
                : Path.Combine(root, "data"));
            Directory.CreateDirectory(dataDirectory);
            await EnsureBackendAsync(root, lifetime.Token);
            message.Text = "正在打开阅读窗口…";
            string cache = Path.Combine(dataDirectory, "webview2");
            Directory.CreateDirectory(cache);
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: cache);
            lifetime.Token.ThrowIfCancellationRequested();
            await browser.EnsureCoreWebView2Async(environment);
            lifetime.Token.ThrowIfCancellationRequested();
            var core = browser.CoreWebView2;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.NavigationStarting += (_, e) =>
            {
                if (IsAppUri(e.Uri)) return;
                e.Cancel = true;
                if (e.IsUserInitiated) OpenExternal(e.Uri);
            };
            core.NewWindowRequested += (_, e) =>
            {
                e.Handled = true;
                if (!e.IsUserInitiated) return;
                if (IsAppUri(e.Uri)) core.Navigate(e.Uri);
                else OpenExternal(e.Uri);
            };
            core.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
            core.DownloadStarting += OnDownloadStarting;
            core.NavigationCompleted += async (_, e) =>
            {
                if (e.IsSuccess)
                {
                    message.Visible = false;
                    browser.Visible = true;
                    browser.Focus();
                    if (smokeFile is not null && !smokeWritten)
                    {
                        smokeWritten = true;
                        await SaveSmokeEvidenceAsync();
                    }
                }
                else
                {
                    message.Text = "阅读窗口加载失败。请关闭后重新打开 PaperLoop。";
                    message.Visible = true;
                    WriteSmokeFailure(e.WebErrorStatus.ToString());
                }
            };
            core.Navigate(appAddress);
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        catch (Exception ex)
        {
            if (IsDisposed) return;
            string detail = ex is WebView2RuntimeNotFoundException
                ? "本机缺少 Microsoft Edge WebView2 Runtime。安装微软官方 WebView2 Runtime 后，再打开程序。"
                : ex.Message;
            message.Text = "PaperLoop 未能启动\n\n" + detail;
            WriteSmokeFailure(detail);
        }
    }

    private bool IsAppUri(string value)
    {
        if (value == "about:blank") return true;
        if (value.StartsWith("blob:", StringComparison.Ordinal)) value = value[5..];
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == "http" &&
               uri.Host == "127.0.0.1" && uri.Port == appPort && string.IsNullOrEmpty(uri.UserInfo);
    }

    private static void OpenExternal(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "https" && uri.Scheme != "http")) return;
        try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception ex) { MessageBox.Show("外部链接未能打开：" + ex.Message, "PaperLoop"); }
    }

    private void OnDownloadStarting(object? sender, CoreWebView2DownloadStartingEventArgs e)
    {
        if (!IsAppUri(e.DownloadOperation.Uri))
        {
            e.Cancel = true;
            e.Handled = true;
            return;
        }
        e.Handled = true;
        var deferral = e.GetDeferral();
        BeginInvoke(() =>
        {
            using (deferral)
            {
                using var save = new SaveFileDialog
                {
                    Title = "保存 PaperLoop 导出文件",
                    FileName = Path.GetFileName(e.ResultFilePath),
                    Filter = "所有文件 (*.*)|*.*",
                    OverwritePrompt = true,
                    RestoreDirectory = true,
                    CheckPathExists = true,
                };
                if (save.ShowDialog(this) == DialogResult.OK) e.ResultFilePath = save.FileName;
                else e.Cancel = true;
            }
        });
    }

    private async Task<bool> HealthyAsync(CancellationToken cancellation)
    {
        try
        {
            using var client = new HttpClient(new HttpClientHandler { UseProxy = false })
            { Timeout = TimeSpan.FromSeconds(2) };
            using var response = await client.GetAsync(appAddress + "api/health", cancellation);
            if (!response.IsSuccessStatusCode) return false;
            using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellation));
            var health = json.RootElement;
            if (!health.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True ||
                !health.TryGetProperty("parser", out _) || !health.TryGetProperty("version", out var version)) return false;
            string expectedEdition = distributed ? "distributed" : "development";
            string actualEdition = health.TryGetProperty("edition", out var edition) ? edition.GetString() ?? "development" : "development";
            if (actualEdition != expectedEdition)
                throw new InvalidOperationException($"端口 {appPort} 已被另一种 PaperLoop 版本占用。请退出该程序后重试。");
            if (distributed && version.GetString() != AppVersion)
                throw new InvalidOperationException($"旧版 PaperLoop 服务仍在端口 {appPort} 运行。请退出旧版程序，等待几秒后重新打开。当前版本：{AppVersion}。");
            return true;
        }
        catch (Exception ex) when (ex is HttpRequestException or JsonException or TaskCanceledException)
        { return false; }
    }

    private async Task EnsureBackendAsync(string root, CancellationToken cancellation)
    {
        if (await HealthyAsync(cancellation)) return;
        message.Text = "正在启动本机论文服务…";
        string data = dataDirectory;
        Directory.CreateDirectory(data);
        FileStream? startLock = null;
        var deadline = DateTime.UtcNow.AddSeconds(60);
        while (startLock is null && DateTime.UtcNow < deadline)
        {
            cancellation.ThrowIfCancellationRequested();
            try
            {
                startLock = new FileStream(Path.Combine(data, "desktop-start.lock"), FileMode.OpenOrCreate,
                    FileAccess.ReadWrite, FileShare.None);
            }
            catch (IOException) { await Task.Delay(300, cancellation); }
        }
        if (startLock is null) throw new InvalidOperationException("另一个窗口正在启动，请稍后重新打开。");
        using (startLock)
        {
            if (await HealthyAsync(cancellation)) return;
            if (IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners().Any(address => address.Port == appPort))
                throw new InvalidOperationException($"本机端口 {appPort} 正被其他程序使用。请关闭占用该端口的程序后重试。");
            string executable = distributed ? Path.Combine(root, "backend", "PaperLoop.Backend.exe")
                : Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            if (!distributed && !File.Exists(executable)) executable = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(executable))
                throw new InvalidOperationException(distributed
                    ? "安装文件不完整，缺少本地论文服务。请重新运行 PaperLoop 安装包修复。"
                    : "当前目录缺少 Python 运行环境。请使用本机完整交付目录启动。");
            var info = new ProcessStartInfo(executable)
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            if (!distributed) info.ArgumentList.Add(Path.Combine(root, "desktop", "launch_backend.py"));
            info.Environment["PAPERLOOP_DATA"] = data;
            info.Environment["PAPERLOOP_PORT"] = appPort.ToString();
            info.Environment["PAPERLOOP_DISTRIBUTED"] = distributed ? "1" : "0";
            if (distributed) info.Environment["PAPERLOOP_PARENT_PID"] = Environment.ProcessId.ToString();
            else info.Environment.Remove("PAPERLOOP_PARENT_PID");
            using var process = Process.Start(info) ?? throw new InvalidOperationException("无法启动本机服务。");
            deadline = DateTime.UtcNow.AddSeconds(60);
            while (DateTime.UtcNow < deadline)
            {
                cancellation.ThrowIfCancellationRequested();
                if (await HealthyAsync(cancellation)) return;
                if (process.HasExited)
                    throw new InvalidOperationException($"本机服务启动失败。请查看日志：\n{Path.Combine(data, "desktop-backend.log")}");
                await Task.Delay(400, cancellation);
            }
            throw new InvalidOperationException($"本机服务启动超时。请关闭后重新打开，或查看日志：\n{Path.Combine(data, "desktop-backend.log")}");
        }
    }

    private async Task SaveSmokeEvidenceAsync()
    {
        try
        {
            await Task.Delay(1500, lifetime.Token);
            string state = await browser.CoreWebView2.ExecuteScriptAsync(
                "JSON.stringify({title:document.title,bodyText:document.body.innerText.slice(0,1500),root:!!document.getElementById('root')})");
            string screenshot = Path.ChangeExtension(smokeFile!, ".png");
            Directory.CreateDirectory(Path.GetDirectoryName(smokeFile!)!);
            using (var file = File.Create(screenshot))
                await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, file);
            string windowScreenshot = Path.ChangeExtension(smokeFile!, ".window.png");
            var visibleBounds = Rectangle.Intersect(Bounds, Screen.FromHandle(Handle).Bounds);
            using (var windowImage = new Bitmap(visibleBounds.Width, visibleBounds.Height))
            {
                using var capture = Graphics.FromImage(windowImage);
                capture.CopyFromScreen(visibleBounds.Location, Point.Empty, visibleBounds.Size);
                windowImage.Save(windowScreenshot, System.Drawing.Imaging.ImageFormat.Png);
            }
            string iconScreenshot = Path.ChangeExtension(smokeFile!, ".icon.png");
            using (var iconImage = (Icon ?? SystemIcons.Application).ToBitmap())
                iconImage.Save(iconScreenshot, System.Drawing.Imaging.ImageFormat.Png);
            await File.WriteAllTextAsync(smokeFile!, JsonSerializer.Serialize(new
            {
                ok = true,
                location = browser.Source.AbsoluteUri,
                browserVersion = browser.CoreWebView2.Environment.BrowserVersionString,
                state = JsonSerializer.Deserialize<string>(state),
                screenshot,
                windowScreenshot,
                iconScreenshot,
                backendHealthy = await HealthyAsync(lifetime.Token),
                edition = distributed ? "distributed" : "development",
                version = AppVersion,
                dataDirectory,
            }, new JsonSerializerOptions { WriteIndented = true }));
            Close();
        }
        catch (Exception ex) { WriteSmokeFailure(ex.Message); }
    }

    private void WriteSmokeFailure(string detail)
    {
        if (smokeFile is null) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(smokeFile)!);
            File.WriteAllText(smokeFile, JsonSerializer.Serialize(new { ok = false, error = detail }));
        }
        finally { BeginInvoke(Close); }
    }
}
