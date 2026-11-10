@echo off
setlocal
cd /d "%~dp0"
set "RUNTIME_DIR=%~dp0runtime"
set "FLAG=%RUNTIME_DIR%\show_window.flag"

if not exist "%RUNTIME_DIR%" (
  mkdir "%RUNTIME_DIR%"
)

> "%FLAG%" echo show

if exist "%FLAG%" (
  echo ============================================================
  echo  [OK] Da gui lenh mo lai cua so DuNoPro Thai Binh!
  echo  Cua so se tu dong hien len trong giay lat...
  echo ============================================================
  timeout /t 1 >nul 2>&1
) else (
  echo.
  echo [LOI] Khong tao duoc file flag de mo lai cua so.
  pause
)
