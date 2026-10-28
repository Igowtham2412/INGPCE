$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    $resolvedCloudflared = Get-Command "cloudflared.exe" -ErrorAction SilentlyContinue
    if ($resolvedCloudflared) {
        $cloudflared = $resolvedCloudflared.Source
    }
}

$logDirectory = Join-Path $env:LOCALAPPDATA "INGPCE\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$watchdogLog = Join-Path $logDirectory "background-watchdog.log"
$dashboardProcess = $null
$dashboardStartedAt = $null
$dashboardRetryAfter = Get-Date
$tunnelRetryAfter = Get-Date
$tunnelProcess = $null

function Write-WatchdogLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $watchdogLog -Value $line
}

function Test-LocalPort {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::1", "::") } |
        Select-Object -First 1)
}

function Get-ConfiguredTunnelProcesses {
    $tunnels = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue
    return @($tunnels | Where-Object {
        $_.CommandLine -match 'tunnel' -and
        $_.CommandLine -match '127\.0\.0\.1:8080'
    })
}

function Get-TunnelLogForProcess {
    param($Process)
    $candidates = Get-ChildItem -LiteralPath $logDirectory `
        -Filter "cloudflared-*.stderr.log" -File -ErrorAction SilentlyContinue
    $bestFile = $null
    $bestDifference = [double]::MaxValue
    foreach ($file in $candidates) {
        if ($file.BaseName -notmatch '^cloudflared-(\d{8}-\d{6})\.stderr$') { continue }
        try {
            $logStart = [datetime]::ParseExact(
                $Matches[1], "yyyyMMdd-HHmmss",
                [Globalization.CultureInfo]::InvariantCulture
            )
            $difference = [Math]::Abs(($Process.CreationDate - $logStart).TotalSeconds)
            if ($difference -le 180 -and $difference -lt $bestDifference) {
                $bestFile = $file
                $bestDifference = $difference
            }
        } catch {
            continue
        }
    }
    return $bestFile
}

function Get-TunnelHealth {
    param($Process)
    $log = Get-TunnelLogForProcess -Process $Process
    if (-not $log) {
        if (((Get-Date) - $Process.CreationDate).TotalSeconds -lt 90) {
            return [pscustomobject]@{ State = "starting"; Process = $Process; Url = $null }
        }
        return [pscustomobject]@{ State = "unhealthy"; Process = $Process; Url = $null }
    }
    $lines = @(Get-Content -LiteralPath $log.FullName -Tail 500 -ErrorAction SilentlyContinue)
    $registeredIndex = -1
    $errorIndex = -1
    $url = $null
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
            $url = $Matches[0]
        }
        if ($lines[$index] -match 'INF Registered tunnel connection') {
            $registeredIndex = $index
        }
        if ($lines[$index] -match 'ERR |Unauthorized: Tunnel not found') {
            $errorIndex = $index
        }
    }
    if ($registeredIndex -gt $errorIndex) {
        return [pscustomobject]@{ State = "healthy"; Process = $Process; Url = $url }
    }
    if (((Get-Date) - $Process.CreationDate).TotalSeconds -lt 90) {
        return [pscustomobject]@{ State = "starting"; Process = $Process; Url = $url }
    }
    return [pscustomobject]@{ State = "unhealthy"; Process = $Process; Url = $url }
}

function Test-ConfiguredDashboardRunning {
    $pythonProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^python(?:3\.\d+)?\.exe$' }
    foreach ($process in $pythonProcesses) {
        if ($process.CommandLine -match '(?:-m\s+app\.main|uvicorn\s+app\.main:app)') {
            return $true
        }
    }
    return $false
}

function Start-Dashboard {
    $script:dashboardRetryAfter = (Get-Date).AddSeconds(60)
    if (-not (Test-Path $python)) {
        Write-WatchdogLog "Cannot start dashboard: project venv Python is missing at $python"
        return
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logDirectory "dashboard-$stamp.stdout.log"
    $stderr = Join-Path $logDirectory "dashboard-$stamp.stderr.log"
    try {
        $process = Start-Process -FilePath $python `
            -ArgumentList @("-m", "app.main") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru
        $script:dashboardProcess = $process
        $script:dashboardStartedAt = Get-Date
        Write-WatchdogLog "Started dashboard process PID $($process.Id); output: $stdout and $stderr"
    } catch {
        Write-WatchdogLog "Dashboard start failed: $($_.Exception.Message)"
    }
}

function Start-CloudflareTunnel {
    $script:tunnelRetryAfter = (Get-Date).AddSeconds(60)
    if (-not (Test-Path $cloudflared)) {
        Write-WatchdogLog "Cannot start Cloudflare Tunnel: cloudflared.exe was not found"
        return
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logDirectory "cloudflared-$stamp.stdout.log"
    $stderr = Join-Path $logDirectory "cloudflared-$stamp.stderr.log"
    try {
        $process = Start-Process -FilePath $cloudflared `
            -ArgumentList @(
                "tunnel", "--url", "http://127.0.0.1:8080",
                "--protocol", "http2", "--no-autoupdate"
            ) `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru
        $script:tunnelProcess = $process
        Write-WatchdogLog "Started Cloudflare quick tunnel PID $($process.Id); output: $stdout and $stderr"
    } catch {
        Write-WatchdogLog "Cloudflare Tunnel start failed: $($_.Exception.Message)"
    }
}

Write-WatchdogLog "Background watchdog started."
while ($true) {
    try {
        if (-not (Test-LocalPort -Port 8080)) {
            $ownedProcessRunning = $false
            if ($dashboardProcess) {
                $dashboardProcess.Refresh()
                $ownedProcessRunning = -not $dashboardProcess.HasExited
            }
            if ($ownedProcessRunning -and
                ((Get-Date) - $dashboardStartedAt).TotalSeconds -gt 300) {
                Write-WatchdogLog "Dashboard PID $($dashboardProcess.Id) did not open port 8080 within five minutes; restarting that PID."
                Stop-Process -Id $dashboardProcess.Id -Force -ErrorAction SilentlyContinue
                $dashboardProcess = $null
                $ownedProcessRunning = $false
            }
            if (-not $ownedProcessRunning -and
                (-not (Test-ConfiguredDashboardRunning)) -and
                (Get-Date) -ge $dashboardRetryAfter) {
                Start-Dashboard
            }
        } else {
            $dashboardProcess = $null
        }
        $tunnelProcesses = @(Get-ConfiguredTunnelProcesses)
        if ($tunnelProcesses.Count -gt 0) {
            $tunnelHealth = Get-TunnelHealth -Process $tunnelProcesses[0]
            if ($tunnelHealth.State -eq "unhealthy") {
                Write-WatchdogLog "Cloudflare Tunnel PID $($tunnelHealth.Process.ProcessId) is disconnected or expired; restarting the configured quick tunnel."
                Stop-Process -Id $tunnelHealth.Process.ProcessId -Force -ErrorAction SilentlyContinue
                $tunnelProcess = $null
                $tunnelRetryAfter = Get-Date
                $tunnelProcesses = @()
            } elseif ($tunnelHealth.State -eq "starting") {
                Write-WatchdogLog "Cloudflare Tunnel PID $($tunnelHealth.Process.ProcessId) is still connecting."
            } else {
                $tunnelProcess = $null
            }
        }
        if ($tunnelProcesses.Count -eq 0 -and (Get-Date) -ge $tunnelRetryAfter) {
            Start-CloudflareTunnel
        }
    } catch {
        Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 15
}
