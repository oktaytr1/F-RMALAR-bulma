# Firma Bulucu — Chrome (gerekirse) + Streamlit paneli (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "venv yok. Once kurulum.bat calistirin."
    exit 1
}

$Port = 9222
if (Test-Path "config.yaml") {
    $m = Select-String -Path "config.yaml" -Pattern "^\s*debug_port:\s*(\d+)" | Select-Object -First 1
    if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
}

$listening = netstat -an | Select-String ":$Port " | Select-String "LISTENING"
if (-not $listening) {
    Write-Host "Chrome debug baslatiliyor (port $Port)..."
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($chrome) {
        Start-Process $chrome -ArgumentList @(
            "--remote-debugging-port=$Port",
            "--user-data-dir=$env:USERPROFILE\chrome_selenium"
        )
        Start-Sleep -Seconds 2
    } else {
        Write-Host "UYARI: Chrome bulunamadi. Site bul icin manuel debug Chrome acin."
    }
} else {
    Write-Host "Chrome debug zaten acik (port $Port)."
}

Write-Host "Panel aciliyor - http://localhost:8501"
Start-Process "http://localhost:8501"
& ".\venv\Scripts\python.exe" -m streamlit run panel.py --server.headless true
