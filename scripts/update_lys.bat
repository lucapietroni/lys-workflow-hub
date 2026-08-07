@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM LYS Workflow Hub - Script di aggiornamento (Windows)
REM
REM USO:
REM   1. Scarica il nuovo zip da GitHub.
REM   2. Estrai il contenuto in  C:\LYSApp\lys-workflow-hub-new\
REM   3. Doppio click su questo file.
REM
REM Lo script:
REM   - ferma il task "LYS Workflow Hub"
REM   - sposta .env e .venv dalla vecchia versione alla nuova
REM   - archivia la vecchia versione in C:\LYSApp\lys-workflow-hub-backup-<ts>\
REM   - installa eventuali nuove dipendenze Python
REM   - riavvia il task
REM
REM Se qualcosa va storto, la cartella di backup contiene la versione
REM precedente intatta.
REM ============================================================================

set "OLD=C:\LYSApp\lys-workflow-hub"
set "NEW=C:\LYSApp\lys-workflow-hub-new"

echo.
echo === LYS Workflow Hub: aggiornamento ===
echo.

REM ---- Sanity check ----
if not exist "%NEW%\src" (
    echo ERRORE: %NEW%\src non esiste.
    echo Estrai prima il nuovo ZIP in C:\LYSApp\lys-workflow-hub-new\
    echo La cartella deve contenere src\, requirements.txt, pyproject.toml ecc.
    echo.
    pause
    exit /b 1
)

if not exist "%OLD%" (
    echo ATTENZIONE: %OLD% non esiste. Nessuna versione precedente da migrare.
    echo Verra' semplicemente installata la nuova.
    move "%NEW%" "%OLD%"
    goto :install_deps
)

REM ---- Calcola timestamp per il backup ----
REM wmic non e' piu' disponibile in Windows 11/Server 2022. Usiamo PowerShell,
REM che produce sempre yyyyMMdd-HHmmss indipendentemente dal formato regionale.
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd-HHmmss'"`) do set "TS=%%i"
if not defined TS (
    echo ATTENZIONE: impossibile generare il timestamp via PowerShell. Uso fallback.
    set "TS=manuale"
)
set "BACKUP=C:\LYSApp\lys-workflow-hub-backup-!TS!"

REM ---- Ferma il task ----
echo Fermo task "LYS Workflow Hub" (se attivo)...
schtasks /End /TN "LYS Workflow Hub" >nul 2>&1
timeout /t 2 /nobreak >nul

REM ---- Termina eventuali processi Python residui dell'app ----
REM schtasks /End da solo spesso non basta: puo' restare un python.exe
REM residuo che tiene .venv/i file dell'app aperti e blocca i move() piu'
REM sotto. Scoped sul CommandLine (non un taskkill /IM python.exe cieco,
REM che ammazzerebbe anche altri script Python eventualmente in corso
REM sulla macchina) - matcha solo processi lanciati dentro C:\LYSApp.
REM Solo apici singoli dentro -Command: con for /f + backquote, i doppi
REM apici annidati vengono corrotti da un secondo giro di parsing di cmd.
echo Termino eventuali processi Python residui dell'app...
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*LYSApp*' } | Select-Object -ExpandProperty ProcessId"`) do (
    echo    Termino PID %%p...
    taskkill /F /PID %%p >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ---- Preserva .env ----
if exist "%OLD%\.env" (
    echo Preservo .env...
    copy /Y "%OLD%\.env" "%NEW%\.env" >nul
) else (
    echo ATTENZIONE: %OLD%\.env non trovato. Dovrai ricrearlo a mano.
)

REM ---- Preserva database SQLite ----
if exist "%OLD%\data\lys_hub.db" (
    echo Preservo data\lys_hub.db ...
    if not exist "%NEW%\data" mkdir "%NEW%\data"
    copy /Y "%OLD%\data\lys_hub.db" "%NEW%\data\lys_hub.db" >nul
) else (
    echo ATTENZIONE: %OLD%\data\lys_hub.db non trovato. Verra' creato vuoto al primo avvio.
)

REM ---- Preserva .venv (rinomina, non copia: e' grosso) ----
if exist "%OLD%\.venv" (
    echo Sposto .venv nella nuova versione...
    move "%OLD%\.venv" "%NEW%\.venv" >nul
)

REM ---- Backup vecchia versione ----
echo Archivio la vecchia versione in:
echo    !BACKUP!
move "%OLD%" "!BACKUP!" >nul
if errorlevel 1 (
    echo ERRORE: impossibile spostare la vecchia versione.
    echo Verifica che nessun programma stia tenendo aperto un file in %OLD%.
    pause
    exit /b 2
)

REM ---- Installa nuova versione ----
echo Installo la nuova versione in %OLD%...
move "%NEW%" "%OLD%" >nul

:install_deps
REM ---- Aggiorna dipendenze ----
cd /d "%OLD%"
if exist ".venv\Scripts\activate.bat" (
    echo Aggiorno dipendenze Python...
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    pip install -e . --no-deps
) else (
    echo ATTENZIONE: .venv non presente.
    echo Crealo manualmente:
    echo    python -m venv .venv
    echo    .venv\Scripts\activate
    echo    pip install -r requirements.txt
    echo    pip install -e .
    pause
    exit /b 3
)

REM ---- Riavvia task ----
echo Riavvio task "LYS Workflow Hub"...
schtasks /Run /TN "LYS Workflow Hub" >nul 2>&1
if errorlevel 1 (
    echo ATTENZIONE: schtasks /Run ha restituito errore.
    echo Forse il task ha un nome diverso, oppure non e' ancora stato creato.
    echo Avvia l'app manualmente:  python -m lys_workflow_hub.main
)

echo.
echo === Aggiornamento completato ===
echo Backup della versione precedente:
echo    !BACKUP!
echo Se l'app funziona, dopo qualche giorno puoi cancellare il backup.
echo.
pause
endlocal
