@echo off
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw web_console_v1\app.py
    exit /b
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw web_console_v1\app.py
    exit /b
)

echo 未找到 pythonw/pyw，改用普通 Python 启动。
python web_console_v1\app.py
