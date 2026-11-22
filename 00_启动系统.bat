@echo off
chcp 65001 >nul
setlocal
title 中考录取模拟系统
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未找到项目 Python 环境，请查看 README.md 中的部署说明。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B manage.py web --open-browser --credential-file "config\password.txt"
if errorlevel 1 (
    echo.
    echo 启动失败，请保留上方错误提示以便排查。
    pause
    exit /b 1
)
endlocal
