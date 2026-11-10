@echo off
setlocal enabledelayedexpansion
title DuNoPro - Dien Luc Thai Binh
cd /d "%~dp0"

:: Kiem tra tham so chay debug (neu muon xem console log truc tiep)
set "DEBUG_MODE=0"
if /i "%~1"=="--debug" set "DEBUG_MODE=1"
if /i "%~1"=="/debug" set "DEBUG_MODE=1"
if /i "%~1"=="-d" set "DEBUG_MODE=1"

:: Lam sach dau ngoac kep loi trong bien PATH cua he thong
set "PATH=%PATH:"=%"

:: Tim kiem trinh thong dich Python va Pythonw tu cung mot moi truong
set "PYTHON_EXE="
set "PYTHONW_EXE="

:: 1. Uu tien moi truong virtual environment dang activate (neu co)
if defined VIRTUAL_ENV (
    if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
        set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
        if exist "%VIRTUAL_ENV%\Scripts\pythonw.exe" (
            set "PYTHONW_EXE=%VIRTUAL_ENV%\Scripts\pythonw.exe"
        )
    )
)

:: 2. Tim python.exe va phan giai duong dan tuyet doi tu PATH
if not defined PYTHON_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_EXE (
            set "PYTHON_EXE=%%~fI"
            if exist "%%~dpIpythonw.exe" (
                set "PYTHONW_EXE=%%~dpIpythonw.exe"
            )
        )
    )
)

:: 3. Fallback duong dan Python 3.11 trong LocalAppData neu tren PATH khong co
if not defined PYTHON_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" (
            set "PYTHONW_EXE=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
        )
    )
)

:: 4. Fallback launcher py / pyw
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "PYTHON_EXE=py"
        where pyw >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "PYTHONW_EXE=pyw"
        )
    )
)

:: Kiem tra neu khong tim thay bat ky trinh thong dich Python nao
if not defined PYTHON_EXE (
    echo.
    echo ============================================================
    echo  [LOI] KHONG TIM THAY TRINH THONG DICH PYTHON!
    echo ============================================================
    echo  He thong khong tim thay lenh 'python' hoac 'pythonw' tren may.
    echo  Vui long kiem tra lai cai dat Python 3.11 hoac PATH.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

:: Che do DEBUG: Chay co console terminal de theo doi truc tiep
if "!DEBUG_MODE!"=="1" (
    echo [DEBUG] Dang khoi dong DuNoPro kem cua so console...
    "!PYTHON_EXE!" dunopro_app.py --debug
    set "APP_EXIT=!ERRORLEVEL!"
    if !APP_EXIT! neq 0 (
        echo.
        echo ============================================================
        echo  [THONG BAO] Ung dung dung voi ma thoat: !APP_EXIT!
        echo ============================================================
        echo.
        pause
    )
    exit /b !APP_EXIT!
)

:: Che do tieu chuan: Uu tien pythonw.exe tu cung moi truong da chon
if defined PYTHONW_EXE (
    start "" "!PYTHONW_EXE!" dunopro_app.py
) else (
    start "" "!PYTHON_EXE!" dunopro_app.py
)

exit /b 0
