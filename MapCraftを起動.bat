@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0MapCraft.exe" (
 echo Extract the complete Creator ZIP before starting MapCraft.
 pause
 exit /b 1
)
if not exist "%~dp0engine\arnis-windows.exe" (
 echo The engine folder is missing. Extract the complete Creator ZIP.
 pause
 exit /b 1
)
start "" /D "%~dp0" "%~dp0MapCraft.exe"
