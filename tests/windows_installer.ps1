# Actual one-click install, installed-app launch, upgrade and uninstall on CI.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$setup = @(Get-ChildItem (Join-Path $repo 'artifacts/CraftFetch-Setup-*-Windows-x64.exe'))
if ($setup.Count -ne 1) { throw 'Expected exactly one installer' }
$installDir = Join-Path $env:LOCALAPPDATA 'Programs/CraftFetch'
$exe = Join-Path $installDir 'CraftFetch.exe'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'CraftFetch.lnk'
$menuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'CraftFetch/CraftFetch.lnk'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B11481EC-09B3-4B5B-9706-4D1B3D745CB9}_is1'
$sentinel = Join-Path $env:RUNNER_TEMP 'craftfetch-game-files/versions/keep.txt'
New-Item -ItemType Directory -Force (Split-Path $sentinel) | Out-Null
Set-Content $sentinel 'keep downloaded game files'

function Stop-InstalledApp {
    Get-Process -Name CraftFetch -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
}

function Assert-Installed {
    foreach ($file in @($exe, $desktopLink, $menuLink, (Join-Path $installDir 'LICENSE'))) {
        if (-not (Test-Path $file)) { throw "Installation did not create $file" }
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($link in @($desktopLink, $menuLink)) {
        if ($shell.CreateShortcut($link).TargetPath -ne $exe) { throw "Wrong shortcut target: $link" }
    }
    $record = Get-ItemProperty $uninstallKey
    if ($record.DisplayName -ne 'CraftFetch') { throw 'Missing uninstall registration' }
}

try {
    # Inno's VCL buttons do not expose UI Automation InvokePattern reliably.
    # Locate the visible native button by its text and invoke its actual click.
    Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CraftFetchInstallerUi {
    private delegate bool EnumProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr data);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] private static extern bool IsWindowEnabled(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr w, IntPtr l);
    public static string Text(IntPtr window) {
        var text = new StringBuilder(1024);
        GetWindowText(window, text, text.Capacity);
        return text.ToString();
    }
    public static IntPtr FindButton() {
        IntPtr result = IntPtr.Zero;
        EnumWindows((window, data) => {
            if (IsWindowVisible(window) && Text(window).Contains("CraftFetch")) {
                EnumChildWindows(window, (child, unused) => {
                    var name = new StringBuilder(128);
                    GetClassName(child, name, name.Capacity);
                    if (IsWindowVisible(child) && IsWindowEnabled(child) &&
                        name.ToString().Contains("Button") && Text(child).Contains("一键安装")) {
                        SetForegroundWindow(window);
                        result = child;
                        return false;
                    }
                    return true;
                }, IntPtr.Zero);
            }
            return result == IntPtr.Zero;
        }, IntPtr.Zero);
        return result;
    }
    public static void Click(IntPtr button) {
        SendMessage(button, 0x00F5, IntPtr.Zero, IntPtr.Zero); // BM_CLICK
    }
}
'@
    $process = Start-Process $setup[0].FullName -ArgumentList @('/NORESTART', "/LOG=$env:RUNNER_TEMP/craftfetch-install.log") -PassThru
    $deadline = (Get-Date).AddSeconds(45)
    $button = [IntPtr]::Zero
    while ((Get-Date) -lt $deadline -and $button -eq [IntPtr]::Zero) {
        $button = [CraftFetchInstallerUi]::FindButton()
        if ($button -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 300 }
    }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $screen = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($screen.Left, $screen.Top, 0, 0, $bitmap.Size)
    $bitmap.Save((Join-Path $repo 'artifacts/installer-ready.png'))
    $graphics.Dispose()
    $bitmap.Dispose()
    if ($button -eq [IntPtr]::Zero) { throw 'One-click install button did not appear' }
    Write-Output "Clicking native installer button: $([CraftFetchInstallerUi]::Text($button))"
    [CraftFetchInstallerUi]::Click($button)
    if (-not $process.WaitForExit(90000)) { throw 'Installer did not finish after one click' }
    if ($process.ExitCode -ne 0) { throw "Install failed: $($process.ExitCode)" }
    Assert-Installed

    $deadline = (Get-Date).AddSeconds(45)
    $launched = $false
    while ((Get-Date) -lt $deadline -and -not $launched) {
        $launched = @(Get-Process -Name CraftFetch -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $exe -and $_.MainWindowTitle -like '*CraftFetch*' }).Count -gt 0
        if (-not $launched) { Start-Sleep -Milliseconds 300 }
    }
    if (-not $launched) { throw 'Installed app did not automatically open its desktop window' }
    Stop-InstalledApp

    # A second install exercises the same AppId and existing installation path.
    $upgrade = Start-Process $setup[0].FullName -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -PassThru
    if (-not $upgrade.WaitForExit(90000) -or $upgrade.ExitCode -ne 0) { throw 'Reinstall failed' }
    Assert-Installed
    $uninstall = Start-Process (Join-Path $installDir 'unins000.exe') -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -PassThru
    if (-not $uninstall.WaitForExit(90000) -or $uninstall.ExitCode -ne 0) { throw 'Uninstall failed' }
    foreach ($file in @($exe, $desktopLink, $menuLink)) {
        if (Test-Path $file) { throw "Uninstall left $file" }
    }
    if (Test-Path $uninstallKey) { throw 'Uninstall registration was not removed' }
    if ((Get-Content $sentinel) -ne 'keep downloaded game files') { throw 'Uninstall changed game files' }
    Write-Output 'One-click install, auto-launch, reinstall, shortcuts and uninstall passed.'
} finally {
    Stop-InstalledApp
    $log = Join-Path $env:RUNNER_TEMP 'craftfetch-install.log'
    if (Test-Path $log) { Get-Content $log -Tail 35 }
}
