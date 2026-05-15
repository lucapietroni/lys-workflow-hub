@echo off
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
start "" .venv\Scripts\pythonw.exe -m lys_workflow_hub.main
exit
