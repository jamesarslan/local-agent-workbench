@echo off
call "%~dp0Run.cmd" "%~dp0desktop_app.py"
if errorlevel 1 pause
