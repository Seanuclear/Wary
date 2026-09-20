@echo off
rem Windows: double-click this file. Builds the site and opens it in your browser.
cd /d "%~dp0"
py preview.py || python preview.py
pause
