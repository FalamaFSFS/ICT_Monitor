@echo off
chcp 65001 >nul
echo ========================================
echo 飞书推送服务启动器
echo ========================================
echo.
echo 按 Ctrl+C 停止服务
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python
    echo 请确保已安装Python并添加到PATH
    pause
    exit /b 1
)

REM 启动飞书推送服务
python feishu_pusher.py

pause
