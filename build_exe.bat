@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version
if errorlevel 1 (
  echo.
  echo Python is not installed. Install it from https://www.python.org/downloads/
  echo and check "Add python.exe to PATH" on the first screen.
  goto fail
)
echo [1/2] Installing packages...
%PY% -m pip install --upgrade mido customtkinter windnd pyinstaller
if errorlevel 1 goto fail
echo [2/2] Building exe...
%PY% -m PyInstaller --noconfirm --onefile --windowed --collect-all customtkinter --name Convert_Midi Convert_Midi_GUI.py
if errorlevel 1 goto fail
echo.
echo Done: dist\Convert_Midi.exe
pause
exit /b 0
:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1
