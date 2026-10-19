$ErrorActionPreference = "Stop"
$taskName = "INGPCE Dashboard and Cloudflare Tunnel"
$logDirectory = Join-Path $env:LOCALAPPDATA "INGPCE\logs"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Output "Scheduled task: $($task.State); last result: $($info.LastTaskResult)"
} else {
    Write-Output "Scheduled task: not installed"
}

foreach ($port in @(8080, 8081)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        Write-Output "Port ${port}: listening (PID $($listener.OwningProcess))"
    } else {
        Write-Output "Port ${port}: not listening"
    }
}

$tunnel = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -match 'tunnel' -and
        $_.CommandLine -match '127\.0\.0\.1:8080'
    } |
    Select-Object -First 1
$url = $null
$tunnelState = "not running"
if ($tunnel) {
    $tunnelState = "starting or disconnected"
    $files = Get-ChildItem -LiteralPath $logDirectory `
        -Filter "cloudflared-*.stderr.log" -File -ErrorAction SilentlyContinue
    $log = $null
    $bestDifference = [double]::MaxValue
    foreach ($file in $files) {
        if ($file.BaseName -notmatch '^cloudflared-(\d{8}-\d{6})\.stderr$') { continue }
        try {
            $logStart = [datetime]::ParseExact(
                $Matches[1], "yyyyMMdd-HHmmss",
                [Globalization.CultureInfo]::InvariantCulture
            )
            $difference = [Math]::Abs(($tunnel.CreationDate - $logStart).TotalSeconds)
            if ($difference -le 180 -and $difference -lt $bestDifference) {
                $log = $file
                $bestDifference = $difference
            }
        } catch {
            continue
        }
    }
    if ($log) {
        $lines = @(Get-Content -LiteralPath $log.FullName -Tail 500 -ErrorAction SilentlyContinue)
        $registeredIndex = -1
        $errorIndex = -1
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
            $tunnelState = "connected"
        }
    }
}
Write-Output "Cloudflare Tunnel: $tunnelState"
if ($url -and $tunnelState -eq "connected") {
    Write-Output "Latest Cloudflare quick-tunnel URL: $url"
} else {
    Write-Output "No active Cloudflare URL is available yet; check the latest tunnel log."
    Write-Output "Log folder: $logDirectory"
}
