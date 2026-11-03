@echo off
setlocal
rem Double-click this file to run the Windows app. No PowerShell policy change.
rem Keep this window open while using the app; Ctrl+C stops the local server.
cd /d "%~dp0"
if not exist "data" mkdir "data"
rem Record whether this exact file was reached during a File Explorer launch.
echo [%date% %time%] Launcher entered.>>"data\launcher-status.log"
set "inventoryPython=%~dp0.venv\Scripts\python.exe"
rem This existing environment was prepared and tested in the current workspace.
rem A copied project uses its own .venv instead, as explained in README.md.
if not exist "%inventoryPython%" set "inventoryPython=%~dp0..\..\work\.venv\Scripts\python.exe"
if not exist "%inventoryPython%" (
    echo Python setup is needed. Follow Windows setup in README.md.
    pause
    exit /b 1
)
"%inventoryPython%" launch.py
set "inventoryExit=%errorlevel%"
echo [%date% %time%] Python returned %inventoryExit%.>>"data\launcher-status.log"
echo.
if "%inventoryExit%"=="0" (
    echo The launcher finished. If the app was already running, its page was opened.
    echo Keep the original server window open while using inventory.
) else (
    echo Inventory could not start. The details are shown above.
    echo Please keep this window open so the error can be checked.
)
rem Always wait, including a successful hand-off to an already running server.
rem Otherwise a double-click can look like a crash when Python exits normally.
pause
exit /b %inventoryExit%
