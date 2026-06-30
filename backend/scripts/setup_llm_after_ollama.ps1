# Ollama 安装后：拉取模型 + 批量 LLM 补全四字段
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$backend = Join-Path $root "backend"
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
$model = "qwen2.5:7b"

if (-not (Test-Path $ollama)) {
    Write-Error "未找到 Ollama。请先运行 backend\scripts\setup_ollama.ps1 或手动安装 OllamaSetup.exe"
}

Write-Host "检查 Ollama 服务..."
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 | Out-Null
} catch {
    Write-Host "启动 Ollama 服务..."
    Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

Write-Host "拉取模型 $model（约 4.7GB，可用 qwen2.5:1.5b 替代）..."
& $ollama pull $model

Write-Host "测试 LLM 抽取..."
$py = Join-Path $backend "venv\Scripts\python.exe"
Push-Location $backend
try {
    & $py -c @"
from config import settings
from crawler.llm_extractor import call_llm_extract
print('LLM enabled:', settings.llm_enabled, 'model:', settings.llm_model)
r = call_llm_extract('测试', '报名开始时间为2026年7月1日，截止时间为7月15日24:00。夏令营举办时间为2026年8月5日至8月7日，采用线上形式。'*2)
print('test result:', r.fields, r.failure_type)
"@
    Write-Host "`n批量补全字段..."
    & $py scripts\llm_enrich_incomplete.py
} finally {
    Pop-Location
}
