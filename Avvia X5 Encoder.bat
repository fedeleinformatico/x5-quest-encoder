@echo off
cd /d "%~dp0"
rem Aggiunge ffmpeg (winget) ed ExifTool al PATH, anche se la sessione e' partita prima dell'installazione.
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%LOCALAPPDATA%\Programs\ExifTool"
python x5_quest_encoder.py
if errorlevel 1 pause
