@echo off
call "%~dp0Run.cmd" "%~dp0cli.py" agent %*
if errorlevel 1 pause
if not errorlevel 0 pause
