@echo off
title SIAT web app
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [XATO] .venv topilmadi. Avval shuni bajaring:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -e .
  pause
  exit /b 1
)
echo Sayt ishga tushmoqda: http://localhost:8000
echo Toxtatish uchun shu oynani yoping yoki Ctrl+C bosing.
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m siat_mcp.api --port 8000
echo.
echo Server toxtadi. Yuqoridagi xabarlarni oqing.
pause
