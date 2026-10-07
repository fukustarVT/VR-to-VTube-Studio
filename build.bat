@echo off
cd /d "%~dp0"
echo This builds the app as executable.
python -m pip install python-osc websockets zeroconf openvr pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "VR to VTube Studio" --icon "icon.ico" --add-data "icon.ico;." --collect-all openvr --collect-all zeroconf vrcft_vtube_app.py
echo.
echo File stored in: dist\VR to VTube Studio.exe
pause