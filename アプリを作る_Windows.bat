@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 資料箱.exe をビルドする (shiryo_box.py / app.html を直したら実行し直す)
if not exist build\venv\Scripts\python.exe (
  echo ビルド用の環境を作っています...
  py -3.11 -m venv build\venv || py -3 -m venv build\venv || goto :fail
)
build\venv\Scripts\python.exe -m pip install -q pywebview pillow pyinstaller || goto :fail
build\venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name 資料箱 --icon "%~dp0icon.ico" --add-data "%~dp0app.html;." ^
  --distpath "%~dp0." --workpath "%~dp0build\pyi" --specpath "%~dp0build" ^
  shiryo_box.py || goto :fail
build\venv\Scripts\python.exe make_licenses.py || goto :fail
echo.
echo できました: %~dp0資料箱.exe
pause
exit /b 0
:fail
echo ビルドに失敗しました
pause
exit /b 1
