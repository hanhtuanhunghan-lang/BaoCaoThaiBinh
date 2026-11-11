@echo off
setlocal enabledelayedexpansion
title Quan Ly User - Dien Luc Thai Binh
cd /d "%~dp0"

:: Lam sach dau ngoac kep loi trong bien PATH cua he thong
set "PATH=%PATH:"=%"

:: Tim kiem Python executable
set "PYTHON_EXE="
where python >nul 2>&1
if !ERRORLEVEL! equ 0 (
    set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    )
)
if not defined PYTHON_EXE (
    if exist "C:\Users\Dell\AppData\Local\Programs\Python\Python311\python.exe" (
        set "PYTHON_EXE=C:\Users\Dell\AppData\Local\Programs\Python\Python311\python.exe"
    )
)
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "PYTHON_EXE=py"
    )
)
if not defined PYTHON_EXE (
    echo.
    echo ============================================================
    echo  [LOI] KHONG TIM THAY PYTHON!
    echo ============================================================
    echo.
    pause
    exit /b 1
)

set DUNOPRO_USER_PROFILE=thai_binh
"!PYTHON_EXE!" QuanLyUser.py thai_binh
set "APP_EXIT=!ERRORLEVEL!"

if !APP_EXIT! neq 0 (
    echo.
    echo [THONG BAO] QuanLyUser dung voi ma thoat: !APP_EXIT!
    pause
)
