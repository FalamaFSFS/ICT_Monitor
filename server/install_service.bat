@echo off
chcp 65001 >nul
echo ========================================
echo 飞书推送服务安装脚本
echo ========================================
echo.
echo 本脚本将配置飞书推送服务为Windows开机自启动
echo.

REM 检查管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo [错误] 需要管理员权限!
    echo 请右键"以管理员身份运行"此脚本
    pause
    exit /b 1
)

set SERVICE_NAME=ICT_FeishuPusher
set SCRIPT_DIR=%~dp0
set PYTHON_SCRIPT=%SCRIPT_DIR%feishu_pusher.py

echo 服务名称: %SERVICE_NAME%
echo Python脚本: %PYTHON_SCRIPT%
echo.

REM 检查Python路径
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i
if not defined PYTHON_PATH (
    echo [错误] 未找到Python!
    pause
    exit /b 1
)
echo Python路径: %PYTHON_PATH%
echo.

REM 使用NSSM创建服务(如果有的话)
where nssm >nul 2>&1
if not errorlevel 1 (
    echo 检测到NSSM,使用NSSM安装服务...
    nssm install %SERVICE_NAME% "%PYTHON_PATH%" "%PYTHON_SCRIPT%"
    nssm set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%"
    nssm set %SERVICE_NAME% Description "ICT Monitor 飞书推送服务"
    nssm set %SERVICE_NAME% Start SERVICE_AUTO_START

    echo.
    echo ✓ 服务安装成功!
    echo.

    choice /C YN /M "是否立即启动服务?"
    if errorlevel 2 goto :end
    if errorlevel 1 (
        nssm start %SERVICE_NAME%
        echo ✓ 服务已启动
    )
    goto :end
)

REM 如果没有NSSM,使用任务计划程序
echo 未检测到NSSM,使用任务计划程序...
echo.
echo 请手动配置任务计划程序:
echo 1. 打开"任务计划程序"
echo 2. 创建基本任务
echo 3. 触发器:计算机启动时
echo 4. 操作:启动程序
echo    程序: %PYTHON_PATH%
echo    参数: "%PYTHON_SCRIPT%"
echo    起始于: %SCRIPT_DIR%
echo.

:end
echo ========================================
echo 安装完成
echo ========================================
pause
