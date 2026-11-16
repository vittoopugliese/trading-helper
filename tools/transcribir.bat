@echo off
rem Transcripcion desatendida. Uso:
rem   tools\transcribir.bat            motor automatico (GPU si anda, si no CPU con 4 hilos)
rem   tools\transcribir.bat 8          CPU con 8 hilos si no hay GPU (PC sin usar)
rem Se puede cerrar la ventana cuando quieras: al relanzar retoma donde quedo.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set THREADS=%1
if "%THREADS%"=="" set THREADS=4
.venv-transcribe\Scripts\python.exe tools\transcribe.py --sources-file tools\transcribe_sources.txt --cpu-threads %THREADS%
echo.
echo Listo. Progreso en knowledge_raw\transcribe.log
pause
