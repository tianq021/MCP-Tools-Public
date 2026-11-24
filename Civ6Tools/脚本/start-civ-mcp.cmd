@echo off
REM ---------------------------------------------------------------
REM  civ6-mcp launcher for Chinese-locale Windows
REM  Forces UTF-8 mode so Python does not default to GBK (cp936).
REM  Without PYTHONUTF8=1 the package crashes at import time.
REM ---------------------------------------------------------------
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=0

REM 8000 falls inside a Windows excluded-port range on this host
REM (7942-8041), where bind fails with winerror 10013. Use a free port.
set CIV_MCP_WEB_PORT=8099

REM 本文件在 E:\Civ6\脚本\ 下，civ6-mcp 在上一级
"%~dp0..\civ6-mcp\.venv\Scripts\civ-mcp.exe" %*
