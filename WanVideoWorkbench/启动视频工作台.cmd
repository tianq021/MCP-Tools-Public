@echo off
setlocal
cd /d "%~dp0"
set "WAN_PYTHON=F:\models\ComfyUI_windows_portable\python_embeded\python.exe"
if not exist "%WAN_PYTHON%" (
  echo Missing Wan Python environment: "%WAN_PYTHON%"
  pause
  exit /b 1
)
"%WAN_PYTHON%" "%~dp0serve.py" %*
if errorlevel 1 pause
