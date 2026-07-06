@echo off
rem BrivMasterPython launcher. Double-click = Home GUI; or pass a command:
rem   run.bat home | farm | monitor | probe | setup   (+ any extra args)
setlocal
cd /d "%~dp0"

set "CMD=%*"
if "%CMD%"=="" set "CMD=home"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run.py %CMD%
    goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python run.py %CMD%
    goto :done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" run.py %CMD%
    goto :done
)
echo No Python found. Install 64-bit Python 3.10+ from https://www.python.org/downloads/
pause

:done
