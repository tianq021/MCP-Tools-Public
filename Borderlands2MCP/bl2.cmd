@echo off
rem BL2 toolkit launcher - pass all arguments to the bl2toolkit CLI
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
cd /d "%~dp0"
"C:\Program Files\Python310\python.exe" -m bl2toolkit %*
endlocal
