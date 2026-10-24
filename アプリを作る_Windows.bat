@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 資料箱.exe を作り直して、使っている exe と配布用 zip を入れ替える (中身は build_app.py)
where py >nul 2>nul
if %errorlevel%==0 (py -3 build_app.py) else (python build_app.py)
pause
