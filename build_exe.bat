@echo off
cd /d "%~dp0"
echo [1/2] Installing packages...
python -m pip install --upgrade mido customtkinter windnd pyinstaller
if errorlevel 1 goto fail
echo [2/2] Building exe...
python -m PyInstaller --noconfirm --onefile --windowed --collect-all customtkinter --name Convert_Midi Convert_Midi_GUI.py
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
