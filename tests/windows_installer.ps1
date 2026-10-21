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
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $process = Start-Process $setup[0].FullName -ArgumentList @('/NORESTART', "/LOG=$env:RUNNER_TEMP/craftfetch-install.log") -PassThru
    $deadline = (Get-Date).AddSeconds(45)
    $button = $null
    $windowCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'CraftFetch 安装')
    $buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    while ((Get-Date) -lt $deadline -and $null -eq $button) {
        $window = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
            [System.Windows.Automation.TreeScope]::Children, $windowCondition)
        if ($null -ne $window) {
            foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)) {
                if ($element.Current.Name -like '*一键安装*') { $button = $element; break }
            }
        }
        if ($null -eq $button) { Start-Sleep -Milliseconds 300 }
    }
    if ($null -eq $button) { throw 'One-click install button did not appear' }
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
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
