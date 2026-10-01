@echo off
cd /d "%~dp0"
rem ffmpeg 8.0.1 locale in testa al PATH (se presente): il driver NVIDIA 591 non regge
rem l'API NVENC 13.1 richiesta da ffmpeg 9.x. Poi ExifTool e ffmpeg di winget come ripiego.
set "PATH=%~dp0ffmpeg-8.0.1;%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%LOCALAPPDATA%\Programs\ExifTool"
python x5_quest_encoder.py
if errorlevel 1 pause
