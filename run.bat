@echo off
title One Click Downloader
color 0b
echo ========================================================
echo        Welcome to One Click Downloader!
echo   Instagram, Facebook & YouTube Video Downloader
echo ========================================================
echo.

cd /d "%~dp0"

echo [1/3] Checking dependencies...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check

echo [2/3] Starting web server...
echo [3/3] Opening browser at http://127.0.0.1:5000 ...

start "" "http://127.0.0.1:5000"
python app.py

pause
