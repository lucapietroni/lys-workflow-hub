@echo off
REM Lancio del job reminder "il giorno prima" per gli appuntamenti di calendario (v3.0 fase 5, parte B).
REM Schedulato da Task Scheduler una volta al giorno (es. 07:00, prima dell'apertura).
REM Esecuzione mono-shot: legge eventi di domani -> notifica admin+esterni -> exit.
REM I log dettagliati finiscono in C:\LYSApp\logs\event_reminders.log (rotazione 5 MB x 5).
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
.venv\Scripts\pythonw.exe scripts\send_event_reminders.py
