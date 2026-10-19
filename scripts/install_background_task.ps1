$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$watchdog = Join-Path $PSScriptRoot "background_services.ps1"
$taskName = "INGPCE Dashboard and Cloudflare Tunnel"
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershell = (Get-Process -Id $PID).Path

if (-not (Test-Path (Join-Path $projectRoot ".venv\Scripts\python.exe"))) {
    throw "Project venv is missing. Create it and install requirements before setting up the background task."
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Project .env is missing. Configure the app before setting up the background task."
}
if (-not (Test-Path $watchdog)) {
    throw "Background watchdog script not found: $watchdog"
}

$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdog`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Keeps the INGPCE dashboard on localhost:8080 and its Cloudflare quick tunnel running outside VS Code." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$task = Get-ScheduledTask -TaskName $taskName
Write-Output "Installed and started: $($task.TaskName)"
Write-Output "Run as: $account (starts when this Windows account signs in)"
Write-Output "Task state: $($task.State)"
Write-Output "Dashboard target: http://127.0.0.1:8080"
Write-Output "Cloudflare output and watchdog logs: $env:LOCALAPPDATA\INGPCE\logs"
