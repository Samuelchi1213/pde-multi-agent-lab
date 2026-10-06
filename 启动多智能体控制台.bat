@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 PDE 多智能体控制台...
python web_console_v1\app.py
pause
