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

echo [2/3] 安装依赖...
call venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo [3/3] 启动服务...
echo.
echo   访问地址: http://127.0.0.1:8000
echo   API 文档: http://127.0.0.1:8000/docs
echo   按 Ctrl+C 停止服务
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
