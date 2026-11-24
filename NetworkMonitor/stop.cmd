@echo off
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo stop>"%~dp0logs\stop.request"
echo Stop requested. The monitor will stop at the next sample.
