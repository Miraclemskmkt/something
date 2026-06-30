@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === 保研夏令营 · Ollama + Qwen2.5 字段补全 ===
echo.
echo [1/2] 安装 Ollama（自动下载失败时可浏览器手动下载后加 -SkipDownload）
powershell -NoProfile -ExecutionPolicy Bypass -File "backend\scripts\setup_ollama.ps1"
if errorlevel 1 (
    echo.
    echo 自动下载失败时，请浏览器下载 OllamaSetup.exe 后执行：
    echo   powershell -ExecutionPolicy Bypass -File backend\scripts\setup_ollama.ps1 -SkipDownload -InstallerPath "完整路径\OllamaSetup.exe"
    pause
    exit /b 1
)
echo.
echo [2/2] 拉取模型并批量 LLM 补全四字段...
powershell -NoProfile -ExecutionPolicy Bypass -File "backend\scripts\setup_llm_after_ollama.ps1"
echo.
pause
