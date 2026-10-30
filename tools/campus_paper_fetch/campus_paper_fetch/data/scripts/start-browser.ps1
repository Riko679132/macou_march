# =====================================================================
# campus-paper-fetch：启动真人 Chrome（cdp 模式前置依赖）— Windows
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File scripts/start-browser.ps1
# =====================================================================
param(
    [int]$Port = 9222
)

$ProfileDir = Join-Path $HOME ".campus-paper-fetch\browser-profile"
$LogFile = Join-Path $env:TEMP "cpf-chrome.log"
$CdpUrl = "http://127.0.0.1:$Port/json/version"

# 探测 Chrome（常见安装路径）
$ChromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$Chrome = $null
foreach ($c in $ChromeCandidates) { if (Test-Path $c) { $Chrome = $c; break } }

# 幂等：CDP 已就绪
try {
    $r = Invoke-WebRequest -Uri $CdpUrl -UseBasicParsing -TimeoutSec 2
    Write-Host "Chrome 已在运行（CDP :$Port），无需重复启动。"
    exit 0
} catch {}

if (-not $Chrome) {
    Write-Error "未找到 Chrome/Edge。请安装 Google Chrome 后重试。"
    exit 1
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
Get-ChildItem -Path $ProfileDir -Filter "Singleton*" -ErrorAction SilentlyContinue | Remove-Item -Force

Write-Host "启动 Chrome（profile: $ProfileDir, CDP: :$Port）..."
$args = @(
    "--user-data-dir=$ProfileDir",
    "--remote-debugging-port=$Port",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-proxy-server"
)
$p = Start-Process -FilePath $Chrome -ArgumentList $args -PassThru

# 等待 CDP 就绪（最多 25 秒）
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-WebRequest -Uri $CdpUrl -UseBasicParsing -TimeoutSec 2
        Write-Host "OK Chrome 已就绪: ws://127.0.0.1:$Port"
        exit 0
    } catch {}
}

Write-Error "等待超时，Chrome 未就绪。"
exit 1
