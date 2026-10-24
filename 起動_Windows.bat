@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py shiryo_box.py) else (python shiryo_box.py)
if errorlevel 9009 echo Python が見つかりません。https://www.python.org/ からインストールしてください。
pause
