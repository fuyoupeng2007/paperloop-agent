using System.Diagnostics;
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
        Application.Run(new ReaderWindow(smokeFile));
    }
}

internal sealed class ReaderWindow : Form
{
    private const string AppAddress = "http://127.0.0.1:8765/";
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

    public ReaderWindow(string? smokeFile)
    {
        this.smokeFile = smokeFile;
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
            // The backend intentionally outlives this window so translation continues.
        };
    }

    private static string FindAppRoot()
    {
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
            await EnsureBackendAsync(root, lifetime.Token);
            message.Text = "正在打开阅读窗口…";
            string cache = Path.Combine(root, "data", "webview2");
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
            core.Navigate(AppAddress);
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

    private static bool IsAppUri(string value)
    {
        if (value == "about:blank") return true;
        if (value.StartsWith("blob:", StringComparison.Ordinal)) value = value[5..];
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == "http" &&
               uri.Host == "127.0.0.1" && uri.Port == 8765 && string.IsNullOrEmpty(uri.UserInfo);
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

    private static async Task<bool> HealthyAsync(CancellationToken cancellation)
    {
        try
        {
            using var client = new HttpClient(new HttpClientHandler { UseProxy = false })
            { Timeout = TimeSpan.FromSeconds(2) };
            using var response = await client.GetAsync(AppAddress + "api/health", cancellation);
            if (!response.IsSuccessStatusCode) return false;
            using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellation));
            return json.RootElement.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True &&
                   json.RootElement.TryGetProperty("parser", out _) && json.RootElement.TryGetProperty("version", out _);
        }
        catch (Exception ex) when (ex is HttpRequestException or JsonException or TaskCanceledException)
        { return false; }
    }

    private async Task EnsureBackendAsync(string root, CancellationToken cancellation)
    {
        if (await HealthyAsync(cancellation)) return;
        message.Text = "正在启动本机论文服务…";
        string data = Path.Combine(root, "data");
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
            string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            if (!File.Exists(python)) python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python))
                throw new InvalidOperationException("当前目录缺少 Python 运行环境。请使用本机完整交付目录启动。");
            var info = new ProcessStartInfo(python)
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            info.ArgumentList.Add(Path.Combine(root, "desktop", "launch_backend.py"));
            info.Environment["PAPERLOOP_DATA"] = data;
            using var process = Process.Start(info) ?? throw new InvalidOperationException("无法启动本机服务。");
            deadline = DateTime.UtcNow.AddSeconds(60);
            while (DateTime.UtcNow < deadline)
            {
                cancellation.ThrowIfCancellationRequested();
                if (await HealthyAsync(cancellation)) return;
                if (process.HasExited)
                    throw new InvalidOperationException("本机服务启动失败。详情在 data/desktop-backend.log。");
                await Task.Delay(400, cancellation);
            }
            throw new InvalidOperationException("本机服务启动超时。稍后重新打开即可继续等待，详情在 data/desktop-backend.log。");
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
