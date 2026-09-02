@echo off
REM Ciclo SDI della contabilita' gestionale (Fase 3).
REM Schedulato da Task Scheduler 1 volta al giorno.
REM Mono-shot: importa attive da WinCar -> invia a SDI -> sincronizza passive -> exit.
REM I log dettagliati finiscono in C:\LYSApp\logs\sdi_poll.log (rotazione 5 MB x 5).
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
.venv\Scripts\pythonw.exe scripts\run_sdi_poll.py
