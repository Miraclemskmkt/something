@echo off
chcp 65001 >nul
echo ========================================
echo   保研夏令营检索平台 - 启动脚本
echo ========================================
echo.

cd /d "%~dp0backend"

if not exist "venv" (
    echo [1/3] 创建 Python 虚拟环境...
    python -m venv venv
)

call venv\Scripts\activate.bat

set "REQ_HASH="
for /f "delims=" %%h in ('certutil -hashfile requirements.txt MD5 ^| findstr /v "hash MD5"') do set "REQ_HASH=%%h"
if not exist ".deps_ok" goto install_deps
set /p OLD_HASH=<.deps_ok
if not "%OLD_HASH%"=="%REQ_HASH%" goto install_deps
echo [2/3] 依赖已就绪，跳过安装
goto start_server

:install_deps
echo [2/3] 安装依赖...
pip install -r requirements.txt -q
echo %REQ_HASH%> .deps_ok

:start_server
echo [3/3] 启动服务...
echo.
echo   访问地址: http://127.0.0.1:8000
echo   API 文档: http://127.0.0.1:8000/docs
echo   按 Ctrl+C 停止服务
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000
