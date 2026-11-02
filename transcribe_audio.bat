@echo off
rem =====================================================================
rem  audio-to-text launcher (Windows)
rem  Use:  drag audio/video files onto this .bat, or run from a terminal:
rem        transcribe_audio.bat "D:\path\audio.mp3" [options]
rem  Results: <this folder>\transcripts\
rem  Note:  all messages are ASCII on purpose (cmd codepage safe);
rem         the Python tool itself prints Chinese/Unicode fine.
rem =====================================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
title audio-to-text

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "TOOL=%ROOT%\.dsh\skills\audio-to-text\scripts\transcribe_audio.py"
set "OUTDIR=%ROOT%\transcripts"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem --- locate the bundled DSH Python 3.12 (runtime may live under a versioned folder)
set "PY="
for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
    if exist "%%~fD\dependencies\python\python.exe" (
        set "PY=%%~fD\dependencies\python\python.exe"
    )
)
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe" (
    set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
)

if not exist "%TOOL%" (
    echo [ERROR] Cannot find transcribe script:
    echo         %TOOL%
    echo         Is the .dsh\skills\audio-to-text folder intact?
    pause
    exit /b 2
)
if not defined PY (
    echo [ERROR] Cannot find the bundled DSH Python runtime:
    echo         %USERPROFILE%\.dsh\dsh-runtimes\*\dependencies\python\python.exe
    echo         Start the tool once from DSH so the runtime is installed.
    pause
    exit /b 2
)

if "%~1"=="" (
    echo.
    echo  audio-to-text  -  local audio/video to text
    echo.
    echo  Drag audio or video files onto this .bat, or run:
    echo    transcribe_audio.bat "D:\path\audio.mp3"
    echo    transcribe_audio.bat "D:\path\audio.mp3" --language zh --format txt,srt
    echo    transcribe_audio.bat "D:\path\folder" --recursive --skip-existing
    echo.
    echo  Options: --language auto^|zh^|en...  --fast  --model large-v3
    echo           --format all^|txt,srt,md,json  --max-seconds N  --help
    echo.
    echo  Results go to: %OUTDIR%
    echo.
    pause
    exit /b 2
)

echo [audio-to-text] engine : %PY%
echo [audio-to-text] tool   : %TOOL%
echo [audio-to-text] output : %OUTDIR%
echo.
"%PY%" "%TOOL%" --output-dir "%OUTDIR%" %*
set "CODE=%ERRORLEVEL%"
echo.
echo [audio-to-text] exit code: %CODE%
echo [audio-to-text] results  : %OUTDIR%
if not "%CODE%"=="0" echo [audio-to-text] some files failed - see messages above.
echo.
if not defined AUDIO2TEXT_NOPAUSE pause
exit /b %CODE%
