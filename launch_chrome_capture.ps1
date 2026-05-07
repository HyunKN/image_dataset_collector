$ErrorActionPreference = "Stop"

$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host "Chrome was not found. Check the Google Chrome install path."
    Read-Host "Press Enter to close"
    exit 1
}

$profileDir = Join-Path $env:TEMP "image_dataset_collector_chrome_capture_profile"
$logFile = Join-Path $PSScriptRoot "chrome_capture_launch.log"
$url = "https://www.google.com/maps?hl=ko"

@(
    "[$(Get-Date)] Launching Chrome",
    "Chrome: $chrome",
    "Profile: $profileDir",
    "URL: $url"
) | Out-File -Encoding utf8 $logFile

Write-Host "Chrome capture profile:"
Write-Host $profileDir
Write-Host ""
Write-Host "1. Open Google Maps review tab or Naver Map visitor review page."
Write-Host "2. Scroll reviews until photos load."
Write-Host "3. Keep this Chrome window open."
Write-Host "4. Run run_collector.bat."
Write-Host ""

$args = @(
    "--new-window",
    "--remote-debugging-port=9222",
    "--user-data-dir=$profileDir",
    $url
)

Start-Process -FilePath $chrome -ArgumentList $args
Start-Sleep -Seconds 4

try {
    $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:9222/json/version" -TimeoutSec 3
    Write-Host "Chrome debug port is ready."
    $response.Content | Out-File -Encoding utf8 $logFile -Append
} catch {
    Write-Host "Chrome debug port is not ready yet."
    Write-Host "If Chrome did not open, check chrome_capture_launch.log."
    $_ | Out-File -Encoding utf8 $logFile -Append
}

Read-Host "Press Enter to close this helper window"
