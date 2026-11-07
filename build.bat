@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

rem 按顺序查找 Python 3.11：环境变量 > py 启动器 > 常见安装目录 > PATH
set "PYTHON="
if defined CF_PYTHON if exist "%CF_PYTHON%" set "PYTHON=%CF_PYTHON%"

if not defined PYTHON (
    for /f "usebackq delims=" %%P in (`py -3.11 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON=%%P"
)

if not defined PYTHON (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Program Files\Python311\python.exe"
        "C:\Python311\python.exe"
    ) do if not defined PYTHON if exist %%P set "PYTHON=%%~P"
)

if not defined PYTHON (
    for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON=%%P"
)

if not defined PYTHON (
    echo [ERROR] 未找到 Python 3.11。
    echo.
    echo 请安装 Python 3.11 后重试，或用环境变量指定解释器：
    echo     set CF_PYTHON=^<python.exe 的完整路径^>
    goto :failed
)

"%PYTHON%" -c "import tkinter, PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 当前 Python 缺少 Tkinter 或 PyInstaller。
    echo 请安装 PyInstaller，或使用带有 Tkinter 的 Python 3.11。
    goto :failed
)

echo [1/4] 运行单元测试...
"%PYTHON%" -m unittest discover -s tests
if errorlevel 1 goto :failed

echo [2/4] 检查资源文件...
if not exist "assets\app.ico" (
    "%PYTHON%" tools\generate_icon.py
    if errorlevel 1 goto :failed
)
if not exist "dist" mkdir "dist"

echo [3/4] 生成版本资源并打包 Windows 应用程序...
"%PYTHON%" tools\gen_version_info.py
if errorlevel 1 goto :failed
if exist "dist\CF优选IP合并工具.exe" (
    move /Y "dist\CF优选IP合并工具.exe" "dist\CF优选IP合并工具.exe.previous" >nul
    if errorlevel 1 (
        echo [ERROR] 旧 exe 正在运行或被占用，请关闭程序后重试。
        goto :failed
    )
)
"%PYTHON%" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed --noupx ^
    --name "CF优选IP合并工具" ^
    --icon "%CD%\assets\app.ico" ^
    --add-data "%CD%\assets\app.ico;assets" ^
    --version-file "%CD%\build_support\version_info.txt" ^
    --distpath "dist" ^
    --workpath "build" ^
    --specpath "build" ^
    app.py
if errorlevel 1 goto :failed

if exist "dist\CF优选IP合并工具.exe.previous" del /Q "dist\CF优选IP合并工具.exe.previous" >nul

echo [4/4] 整理发布文件...
rem 发布包必须使用根目录示例配置，避免把本机测试来源与设置打进去
copy /Y "URL.txt" "dist\URL.txt" >nul
if errorlevel 1 goto :failed
copy /Y "程序配置.json" "dist\程序配置.json" >nul
if errorlevel 1 goto :failed
copy /Y "README.md" "dist\README.md" >nul
if errorlevel 1 goto :failed
if exist "dist\使用说明.md" del /Q "dist\使用说明.md" >nul
"%PYTHON%" tools\package_release.py
if errorlevel 1 goto :failed

echo.
echo 构建完成
echo.
echo   EXE 与发布包目录:
echo   %~dp0dist
echo.
if /I not "%~1"=="--no-pause" pause
exit /b 0

:failed
echo.
echo 构建失败，请查看上方错误信息。
if /I not "%~1"=="--no-pause" pause
exit /b 1
