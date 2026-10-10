@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
%PY% -c "import mido, customtkinter, tkinterdnd2" >nul 2>nul
if not errorlevel 1 goto run
echo Installing required packages...
%PY% -m pip install --upgrade mido customtkinter tkinterdnd2
if errorlevel 1 goto fail
:run
where %PY%w >nul 2>nul
if errorlevel 1 goto console
start "" %PY%w "%~dp0Convert_Midi_GUI.py" %*
exit /b 0
:console
%PY% "%~dp0Convert_Midi_GUI.py" %*
exit /b 0
:nopython
echo.
echo Python is not installed. Install it from https://www.python.org/downloads/
echo and check "Add python.exe to PATH" on the first screen.
:fail
echo.
echo Could not start. Check the messages above.
pause
exit /b 1
