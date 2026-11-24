@echo off
setlocal
set "setup=%~dp0Camera Printer Setup.pyz"
where py.exe >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import tkinter" >nul 2>nul
    if not errorlevel 1 (
        py -3 "%setup%"
        goto finished
    )
)
if exist "%ProgramFiles%\OrcaSlicer\python\python.exe" (
    "%ProgramFiles%\OrcaSlicer\python\python.exe" "%setup%"
    goto finished
)
python "%setup%"
:finished
if errorlevel 1 pause
