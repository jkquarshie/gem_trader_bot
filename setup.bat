@echo off
REM Setup script for Gem Trader Bot
REM Run this from PowerShell or Command Prompt

echo.
echo ========================================
echo GEM TRADER BOT - SETUP
echo ========================================
echo.

REM Create virtual environment
echo Creating virtual environment...
python -m venv venv

REM Activate (for Windows Command Prompt)
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ========================================
echo ✓ SETUP COMPLETE
echo ========================================
echo.
echo To activate the environment next time, run:
echo   venv\Scripts\activate.bat
echo.
echo Then test the rug checker:
echo   python test_rug_checker.py
echo.
