@echo off
chcp 65001 >nul
cd /d "%~dp0"
"F:\conda\envs\python_mmx_LLM\python.exe" -B -X utf8 "%~dp0scripts\unit_duel\launch.py"
if errorlevel 1 pause
