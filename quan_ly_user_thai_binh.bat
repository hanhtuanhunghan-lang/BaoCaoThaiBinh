@echo off
setlocal
title Quan Ly Tai Khoan Web - Dien Luc Thai Binh
cd /d "%~dp0"
set "DUNOPRO_USER_PROFILE=thai_binh"
python QuanLyUser.py thai_binh
if %ERRORLEVEL% neq 0 (
  pause
)
