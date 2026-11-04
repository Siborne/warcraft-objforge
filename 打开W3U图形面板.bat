@echo off
cd /d "%~dp0"
python "%~dp0w3u_gui.py"
if errorlevel 1 pause
