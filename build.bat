@echo off
chcp 65001 >nul
echo ========================================
echo ICT Monitor 自动打包脚本
echo ========================================
echo.

REM 检查是否安装PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [错误] 未安装 PyInstaller
    echo 请运行: pip install pyinstaller
    pause
    exit /b 1
)

echo [1/4] 清理旧的打包文件...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo ✓ 清理完成
echo.

echo [2/4] 打包主程序 (ICT_Monitor.exe)...
pyinstaller main.spec --clean
if errorlevel 1 (
    echo ✗ 主程序打包失败!
    pause
    exit /b 1
)
echo ✓ 主程序打包完成: dist\ICT_Monitor\ICT_Monitor.exe
echo.

echo [3/4] 打包启动器 (ICT_Launcher.exe)...
pyinstaller launcher.spec --clean
if errorlevel 1 (
    echo ✗ 启动器打包失败!
    pause
    exit /b 1
)
echo ✓ 启动器打包完成: dist\ICT_Launcher\ICT_Launcher.exe
echo.

echo [4/4] 复制必要文件到输出目录...

REM 复制 version_manager.py 到主程序目录
copy /y version_manager.py dist\ICT_Monitor\version_manager.py >nul
echo ✓ 已复制 version_manager.py 到主程序目录

REM 复制 version_manager.py 到启动器目录
copy /y version_manager.py dist\ICT_Launcher\version_manager.py >nul
echo ✓ 已复制 version_manager.py 到启动器目录

REM 复制 version.json 到两个目录
copy /y version.json dist\ICT_Monitor\version.json >nul
copy /y version.json dist\ICT_Launcher\version.json >nul
echo ✓ 已复制 version.json

REM 复制 config 目录到两个目录
if exist config (
    xcopy /e /i /y config dist\ICT_Monitor\config >nul
    xcopy /e /i /y config dist\ICT_Launcher\config >nul
    echo ✓ 已复制 config 目录
)

echo.
echo ========================================
echo 打包完成!
echo ========================================
echo.
echo 输出目录:
echo   - 主程序: dist\ICT_Monitor\
echo   - 启动器: dist\ICT_Launcher\
echo.
echo 下一步:
echo   1. 检查 version.json 中的版本号和 build 号
echo   2. 部署主程序到服务器: \\10.174.204.202\tool\ICT_Monitor\
echo   3. 部署启动器到客户端机台
echo.
echo 详细说明请参考: 快速开始.md
echo ========================================
pause
