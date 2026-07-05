@echo off
rem PyBrivMaster setup - double-clickable wrapper around setup_check.py.
rem Finds a suitable Python (py launcher, then PATH) and runs the checker.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 setup_check.py %*
    goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python setup_check.py %*
    goto :done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" setup_check.py %*
    goto :done
)
echo No Python found. Install 64-bit Python 3.10+ from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then run this again.

:done
echo.
pause
