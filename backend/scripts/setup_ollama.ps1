# Ollama 安装脚本（Windows）
# 顺序：winget → aria2/curl 多镜像 → 手动 -SkipDownload
param(
    [string]$InstallerPath = "",
    [switch]$SkipDownload,
    [switch]$SkipWinget
)

$ErrorActionPreference = "Stop"
$version = "v0.30.11"
$official = "https://github.com/ollama/ollama/releases/download/$version/OllamaSetup.exe"

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $env:TEMP "OllamaSetup.exe"
}

$mirrors = @(
    "https://gh.ddlc.top/$official",
    "https://gh.con.sh/$official",
    "https://gh.api.99988866.xyz/$official",
    "https://gh2.yanqishui.xyz/$official",
    "https://ghproxy.net/$official",
    "https://mirror.ghproxy.com/$official",
    "https://kgithub.com/ollama/ollama/releases/download/$version/OllamaSetup.exe",
    $official
)

function Get-OllamaExe {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path ${env:ProgramFiles} "Ollama\ollama.exe")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Test-InstallerComplete([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    return (Get-Item $Path).Length -ge 500MB
}

function Download-Installer([string]$Url, [string]$OutFile) {
    $dir = Split-Path $OutFile -Parent
    $name = Split-Path $OutFile -Leaf
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Push-Location $dir
    try {
        if (Get-Command aria2c -ErrorAction SilentlyContinue) {
            Write-Host "  aria2: $Url"
            aria2c -x 8 -s 8 -k 1M --timeout=30 --max-tries=2 --file-allocation=none -o $name $Url
        } else {
            Write-Host "  curl: $Url"
            curl.exe -L --retry 2 --retry-delay 3 --connect-timeout 10 -o $name $Url
        }
    } finally {
        Pop-Location
    }
}

# 已安装则跳过
$existing = Get-OllamaExe
if ($existing) {
    Write-Host "Ollama 已存在: $existing"
    & $existing --version
    exit 0
}

# 方案一：winget（微软 CDN，国内通常较快）
if (-not $SkipDownload -and -not $SkipWinget -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "`n[1/3] 尝试 winget install Ollama.Ollama ..."
    winget source update | Out-Null
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements 2>&1 | Write-Host
    Start-Sleep -Seconds 5
    $existing = Get-OllamaExe
    if ($existing) {
        Write-Host "winget 安装成功: $existing"
        & $existing --version
        exit 0
    }
    Write-Warning "winget 未成功，继续镜像下载..."
}

# 方案二：多镜像下载
if (-not $SkipDownload) {
    if (Test-InstallerComplete $InstallerPath) {
        Write-Host "已存在完整安装包: $InstallerPath"
    } else {
        if (Test-Path $InstallerPath) {
            Write-Host "删除不完整安装包..."
            Remove-Item $InstallerPath -Force
        }
        Write-Host "`n[2/3] 多镜像下载..."
        $ok = $false
        foreach ($url in $mirrors) {
            Write-Host "Trying: $url"
            try {
                Download-Installer $url $InstallerPath
                if (Test-InstallerComplete $InstallerPath) {
                    Write-Host "Success via $url"
                    $ok = $true
                    break
                }
                Remove-Item $InstallerPath -ErrorAction SilentlyContinue
            } catch {
                Write-Warning "失败: $($_.Exception.Message)"
                Remove-Item $InstallerPath -ErrorAction SilentlyContinue
            }
        }
        if (-not $ok) {
            Write-Host @"

[3/3] 自动下载全部失败。请手动下载后执行：
  powershell -ExecutionPolicy Bypass -File backend\scripts\setup_ollama.ps1 -SkipDownload -InstallerPath "完整路径\OllamaSetup.exe"

推荐链接：
  $official
  https://gh.ddlc.top/$official

或 Docker（需 Docker Desktop）：
  docker run -d --name ollama -p 11434:11434 ollama/ollama:latest
"@
            exit 1
        }
    }
}

if (-not (Test-Path $InstallerPath)) {
    throw "未找到安装包: $InstallerPath"
}

Write-Host "`n静默安装..."
Start-Process -FilePath $InstallerPath -ArgumentList "/S" -Wait
Start-Sleep -Seconds 8

$ollama = Get-OllamaExe
if ($ollama) {
    & $ollama --version
    Write-Host "Ollama 已安装: $ollama"
} else {
    throw "安装后未找到 ollama.exe"
}
