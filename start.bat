@echo off
chcp 65001 >nul
title ShopBot Public Platform
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo Запуск ShopBot через виртуальное окружение .venv...
    ".venv\Scripts\python.exe" main.py
) else (
    echo Запуск ShopBot через системный Python...
    python main.py
)
pause
