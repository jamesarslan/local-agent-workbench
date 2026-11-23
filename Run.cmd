@echo off
setlocal
set "WORKBENCH_CALLER_CWD=%CD%"
cd /d "%~dp0"
if not exist "requirements.lock" goto misplaced
set "supported=import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 14))"
set "ready=import importlib.util as u, os, sys; s = u.find_spec('pip'); d = os.path.dirname(os.path.dirname(s.origin)) if s and s.origin else None; sys.exit(not ((3, 12) <= sys.version_info[:2] <= (3, 14) and d and any(n.startswith('pip-') and os.path.isfile(os.path.join(d, n, 'INSTALLER')) for n in os.listdir(d))))"
set "unfinished=import importlib.util as u, os, shutil, sys; d = os.path.dirname(os.path.dirname(u.find_spec('pip').origin)); [shutil.rmtree(os.path.join(d, n), True) for n in os.listdir(d) if os.path.commonpath([d, sys.prefix]) == sys.prefix and (n[0] == '~' or n.endswith('.dist-info') and not os.path.isfile(os.path.join(d, n, 'INSTALLER')))]"
if not exist ".venv\Scripts\python.exe" goto build
".venv\Scripts\python.exe" -c "%ready%" >nul 2>nul
if errorlevel 1 goto build
if errorlevel 0 goto packages
:build
set "bootstrap="
if exist "python\python.exe" set "bootstrap=%~dp0python\python.exe"
if defined bootstrap goto check
if defined WORKBENCH_BOOTSTRAP_PYTHON set "bootstrap=%WORKBENCH_BOOTSTRAP_PYTHON%"
if defined bootstrap goto check
call :find python
if defined bootstrap goto create
for %%V in (3.12 3.13 3.14) do if not defined bootstrap call :find py -%%V
if defined bootstrap goto create
goto nopython
:check
"%bootstrap%" -c "%supported%" >nul 2>nul
if errorlevel 1 goto badpython
if not errorlevel 0 goto badpython
:create
if not exist ".venv\pyvenv.cfg" goto fresh
echo Rebuilding the Python environment because it was incomplete, moved, or made by another Python.
rmdir /s /q ".venv"
:fresh
if exist ".venv" goto foreign
"%bootstrap%" -m venv ".venv"
if errorlevel 1 goto novenv
if not errorlevel 0 goto novenv
:packages
fc /b requirements.lock ".venv\workbench-requirements.txt" >nul 2>nul
if not errorlevel 1 goto run
echo Installing Python packages from pypi.org (first start and after an update).
".venv\Scripts\python.exe" -I -c "%unfinished%" >nul 2>nul
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input --require-hashes --only-binary :all: -r requirements.lock
if errorlevel 1 goto nopackages
if not errorlevel 0 goto nopackages
copy /y requirements.lock ".venv\workbench-requirements.txt" >nul
:run
".venv\Scripts\python.exe" %*
exit /b %errorlevel%

:find
set "bootstrap="
for /f "delims=" %%P in ('%* -c "import sys; print(sys.executable)" 2^>nul') do set "bootstrap=%%P"
if not defined bootstrap exit /b 0
"%bootstrap%" -c "%supported%" >nul 2>nul
if errorlevel 1 set "bootstrap="
if not errorlevel 0 set "bootstrap="
exit /b 0

:misplaced
echo Start Run.cmd from the Agent Workbench folder itself; a copy of it elsewhere cannot find its files.
exit /b 1
:nopython
echo Agent Workbench needs Python 3.12, 3.13 or 3.14. Install one from python.org, then start again.
exit /b 1
:badpython
echo The Python at "%bootstrap%" did not start or is not version 3.12, 3.13 or 3.14. Download the Windows x64 kit again, or point WORKBENCH_BOOTSTRAP_PYTHON at a supported Python.
exit /b 1
:foreign
echo The .venv folder here could not be replaced. Close every Agent Workbench window, delete "%~dp0.venv" yourself, then start again.
exit /b 1
:novenv
echo Python could not create its environment in "%~dp0.venv". Move Agent Workbench to a folder you can write to, then start again.
exit /b 1
:nopackages
echo The Python packages could not be installed. Check the connection to pypi.org and files.pythonhosted.org, then start again.
exit /b 1
