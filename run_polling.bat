@echo off
REM Lancio del job di polling delle risposte assicurazioni (M3).
REM Schedulato da Task Scheduler 2 volte al giorno (es. 09:00 e 17:00).
REM Esecuzione mono-shot: fetch IMAP -> match -> classify AI -> notifica -> exit.
REM I log dettagliati finiscono in C:\LYSApp\logs\polling.log (rotazione 5 MB x 5).
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
.venv\Scripts\pythonw.exe scripts\run_polling.py
