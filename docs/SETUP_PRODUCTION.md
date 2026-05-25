# Installazione su PC carrozzeria (produzione)

Guida operativa per installare LYS Workflow Hub sul PC dove gira WinCar e farlo
partire automaticamente all'accensione del computer, raggiungibile dai tablet
in LAN aziendale.

> Tempo stimato: 45–60 minuti la prima volta.

## Sommario

1. [Prerequisiti software](#1-prerequisiti-software)
2. [Scaricare il repository](#2-scaricare-il-repository)
3. [Configurazione iniziale](#3-configurazione-iniziale)
4. [Verifica manuale](#4-verifica-manuale)
5. [Avvio automatico (Task Scheduler)](#5-avvio-automatico-task-scheduler)
6. [Firewall LAN](#6-firewall-lan)
7. [Trovare l'indirizzo per i tablet](#7-trovare-lindirizzo-per-i-tablet)
8. [Aggiornare l'app](#8-aggiornare-lapp)
9. [Risoluzione problemi](#9-risoluzione-problemi)

---

## 1. Prerequisiti software

Tutti i seguenti vanno installati **una sola volta**.

### 1.1 Python 3.11+ (64-bit)

Scarica l'installer dal sito ufficiale: <https://www.python.org/downloads/windows/>

Durante l'installazione spunta:

- ✅ **Add python.exe to PATH** (in basso nella prima schermata — è l'opzione più importante).
- ✅ **Install for all users** (consigliato).

Verifica in PowerShell:

```powershell
python --version
```

Deve rispondere con la versione (es. `Python 3.13.0`).

### 1.2 Driver Microsoft Access (64-bit)

Permette a `pyodbc` di leggere i file `.mdb` di WinCar.

Scarica **Microsoft Access Database Engine 2016 Redistributable** (versione **x64**)
dal sito Microsoft: <https://www.microsoft.com/en-us/download/details.aspx?id=54920>

> ⚠️ Se sul PC è installato Office a 32-bit, il driver 64-bit potrebbe
> rifiutare l'installazione. In tal caso esegui da Prompt comandi come admin:
> `AccessDatabaseEngine_X64.exe /quiet`

Verifica in PowerShell:

```powershell
Get-OdbcDriver | Where-Object Name -like "*Access*"
```

Deve elencare `Microsoft Access Driver (*.mdb, *.accdb)`.

### 1.3 Microsoft Word

Serve per la conversione `.docx → PDF` via COM (libreria `docx2pdf`).
Se Office è già installato sul PC, è OK. Altrimenti procurarselo.

> **Nota:** LibreOffice non è supportato. La conversione PDF usa `docx2pdf`
> che richiede Word in sessione utente (vedi §9 in caso di problemi).

> Niente Git sul PC carrozzeria: lo sviluppo gira sul PC dello sviluppatore,
> sul PC carrozzeria ci arriva solo lo ZIP del codice.

---

## 2. Scaricare il repository

### 2.1 Procurati lo ZIP

Sul **PC dello sviluppatore** (non sul PC carrozzeria) apri il repository su
GitHub:

```
https://github.com/lysauto/lys-workflow-hub
```

Bottone verde **"Code" → Download ZIP**. Lo ZIP che ottieni si chiama
`lys-workflow-hub-main.zip`.

Trasferisci lo ZIP al PC carrozzeria con il metodo che preferisci: chiavetta
USB, OneDrive condiviso, condivisione di rete, email.

### 2.2 Estrai sul PC carrozzeria

Crea la cartella `C:\LYSApp\` (se non esiste) ed estrai lì lo ZIP. Rinomina
la cartella estratta in modo che risulti:

```
C:\LYSApp\lys-workflow-hub\
├── README.md
├── pyproject.toml
├── src\
└── …
```

> ℹ️ GitHub mette dentro lo ZIP una cartella tipo `lys-workflow-hub-main`;
> rinominala in `lys-workflow-hub` (senza `-main`) e mettila in `C:\LYSApp\`.

### 2.3 Alternativa: git (solo se preferisci usarlo anche in produzione)

Se invece preferisci avere git anche sul PC carrozzeria (per fare `git pull`
agli aggiornamenti, saltando lo ZIP), installa git da
<https://git-scm.com/download/win> e:

```powershell
mkdir C:\LYSApp
cd C:\LYSApp
git clone https://github.com/lysauto/lys-workflow-hub.git
```

Per il resto della guida assumiamo il metodo ZIP, ma le istruzioni si adattano.

---

## 3. Configurazione iniziale

Tutti i comandi che seguono partono da `C:\LYSApp\lys-workflow-hub`.

### 3.1 Crea l'ambiente virtuale Python

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Se PowerShell rifiuta `Activate.ps1` con un errore di execution policy, esegui
una sola volta:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

E rispondi `S`. Poi riprova l'activate.

### 3.2 Installa le dipendenze

```powershell
pip install -r requirements.txt
pip install -e .
```

La prima esecuzione scarica una trentina di pacchetti e ci mette qualche
minuto. Le successive sono istantanee.

### 3.3 Crea il file `.env`

```powershell
copy .env.example .env
notepad .env
```

Modifica almeno questi valori:

```dotenv
WINCAR_ARCHIVIO=C:\WinCar\Archivi
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8000
APP_ARCHIVIO_CESSIONI=C:\LYSApp\Cessioni_firmate
```

> Le voci `PEC_*`, `EMAIL_*`, `SMTP_*`, `ANTHROPIC_API_KEY`, `NTFY_*` sono
> necessarie per il polling delle risposte (M3/M4). Compilale tutte prima
> di avviare il task di polling (§5.5).

Salva e chiudi il blocco note.

---

## 4. Verifica manuale

Prima di automatizzare l'avvio, verifica che tutto funzioni a mano.

### 4.1 Connessione al DB di WinCar

```powershell
python scripts\test_wincar_connection.py
```

Atteso: stampa lo schema check OK, la lista delle ultime 5 pratiche reali e
i dettagli della più recente. Se vedi un errore "WinCar non raggiungibile" o
"driver non trovato", torna al §1.2.

### 4.2 Test automatici

```powershell
pytest -q
```

Atteso: tutti i test verdi.

### 4.3 Avvio app

```powershell
python -m lys_workflow_hub.main
```

Aspetta `Application startup complete`. Apri il browser su
<http://localhost:8000> — deve apparire la home con il form di ricerca.

Premi `Ctrl+C` per fermarla.

---

## 5. Avvio automatico (Task Scheduler)

Esistono due strade per far partire l'app all'accensione del PC. **Consigliamo
Task Scheduler** perché Word COM (richiesto per generare i PDF) funziona
correttamente in sessione utente e ha problemi se l'app gira come servizio
LocalSystem.

### 5.1 Script di avvio

Lo script `start_lys.bat` è **già incluso nel repository**, nella radice di
`lys-workflow-hub\`. Dopo lo step §2 (download/estrazione ZIP) lo trovi
in `C:\LYSApp\lys-workflow-hub\start_lys.bat`. Non devi crearlo a mano.

Contenuto attuale (a solo titolo informativo, non modificarlo a meno che
non sia strettamente necessario):

```bat
@echo off
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
start "" .venv\Scripts\pythonw.exe -m lys_workflow_hub.main
exit
```

Note tecniche su come è fatto:

- **`pythonw.exe`** (versione GUI di Python) invece di `python.exe`: niente
  finestra cmd visibile durante l'esecuzione in background. I log finiscono
  comunque nel file `C:\LYSApp\logs\lys-hub.log` (vedi §9 Logs).
- **`start ""`** lancia il processo Python in modo asincrono, in modo che
  il `.bat` possa terminare con `exit` immediatamente senza tenere la cmd
  attaccata al server.
- **`set PYTHONPATH=%CD%\src`** è una cintura di sicurezza: anche se in
  futuro qualcuno dimenticasse di rifare `pip install -e .` dopo un
  aggiornamento, Python troverebbe comunque i moduli sotto `src/`.

> 💡 Se vuoi temporaneamente tenere visibile la console (per esempio per
> diagnosticare un problema), puoi creare a fianco un `start_lys_debug.bat`
> con `python.exe` (non `pythonw.exe`) e senza `start ""` né `exit` —
> i log andranno su console + su file. Vedi §9 per dettagli.

### 5.2 Crea il Task Scheduler

Tasto Windows → cerca **"Utilità di pianificazione"** (Task Scheduler) e
aprila.

Nel menu di destra: **Crea attività…** (NON "Crea attività di base").

**Tab "Generale":**

- Nome: `LYS Workflow Hub`
- Descrizione (opzionale): `Server interno per cessioni e workflow carrozzeria`
- Spunta: ☑ **Esegui solo se l'utente ha effettuato l'accesso**
- ☑ **Esegui con privilegi più elevati**
- Configura per: il tuo sistema operativo (Windows 10 / 11)

**Tab "Trigger" → Nuovo…:**

- Avvia attività: **All'accesso** (oppure "All'avvio del sistema" se preferisci)
- Utente specifico: l'utente con cui usate il PC della carrozzeria (es. `CARROZZERIA\Operatore`)
- Spunta: ☑ **Attivata**
- OK

**Tab "Azioni" → Nuovo…:**

- Azione: **Avvia programma**
- Programma/script: `C:\LYSApp\lys-workflow-hub\start_lys.bat`
- "Inizia in" (opzionale ma raccomandato): `C:\LYSApp\lys-workflow-hub`
- OK

**Tab "Condizioni":**

- Disattiva ☐ "Avvia attività solo se il computer è alimentato da rete elettrica"
  (così va anche su laptop a batteria).

**Tab "Impostazioni":**

- ☑ Consenti esecuzione su richiesta
- ☑ Se l'attività non riesce, riavvia ogni: `1 minuto`, fino a `3 volte`

**Salva** (chiederà la password dell'utente Windows).

### 5.3 Avvia subito

Trova `LYS Workflow Hub` nella libreria delle attività di pianificazione, tasto
destro → **Esegui**.

Apri il browser su <http://localhost:8000> per confermare che parte.

### 5.4 Alternativa: NSSM (per i puristi del "servizio Windows")

Se preferisci vederla davvero come servizio Windows (visibile in `services.msc`):

1. Scarica NSSM da <https://nssm.cc/download>, estrai `nssm.exe` in `C:\LYSApp\`.
2. PowerShell come admin:
   ```powershell
   cd C:\LYSApp
   .\nssm install LysWorkflowHub `
       C:\LYSApp\lys-workflow-hub\.venv\Scripts\python.exe `
       -m lys_workflow_hub.main
   .\nssm set LysWorkflowHub AppDirectory C:\LYSApp\lys-workflow-hub
   .\nssm set LysWorkflowHub Start SERVICE_AUTO_START
   .\nssm start LysWorkflowHub
   ```
3. Configura il servizio per girare sotto un utente reale (NON LocalSystem)
   altrimenti la generazione PDF via Word COM non funziona:
   `services.msc` → LysWorkflowHub → Proprietà → tab "Accesso" → "Questo
   account" → utente carrozzeria + password.

---

## 5.5 Task Scheduler per il polling delle risposte (M3)

Oltre al task che avvia l'app web, M3 ha un **secondo task** che gira
2 volte al giorno per scaricare le risposte delle compagnie, classificarle
e mandarti le notifiche.

### Script di lancio

Lo script `run_polling.bat` è **già incluso nel repository**, nella radice
di `lys-workflow-hub\`. Lo trovi in `C:\LYSApp\lys-workflow-hub\run_polling.bat`.
Non devi crearlo a mano.

Contenuto (a solo titolo informativo):

```bat
@echo off
cd /d C:\LYSApp\lys-workflow-hub
set PYTHONPATH=%CD%\src
.venv\Scripts\pythonw.exe scripts\run_polling.py
```

Note tecniche:

- A differenza di `start_lys.bat`, **non usa `start ""` né `exit`**. Il `.bat`
  resta in attesa che `pythonw.exe` termini il ciclo (di norma 30 sec – 2 min),
  così Task Scheduler può vedere il return code e segnalarti eventuali
  esecuzioni andate male.
- Niente finestra cmd visibile (`pythonw.exe`). I log dettagliati di ogni
  ciclo (fetch, match, classify, notify, costo AI) finiscono in
  `C:\LYSApp\logs\polling.log` con rotazione 5 MB × 5 file.

### Task Scheduler

**Utilità di pianificazione** → **Crea attività…**

- **Generale**: nome `LYS Polling Risposte`, ✅ "Esegui solo se l'utente
  ha effettuato l'accesso", ✅ "Esegui con privilegi più elevati".
- **Trigger**: due trigger giornalieri, "Ogni giorno alle 09:00" e
  "Ogni giorno alle 17:00". (Aggiusta gli orari a piacere — l'importante
  è che il PC sia acceso a quegli orari.)
- **Azioni**: avvia `C:\LYSApp\lys-workflow-hub\run_polling.bat`,
  "Inizia in" = `C:\LYSApp\lys-workflow-hub`.
- **Condizioni**: togli la spunta a "Avvia attività solo se il computer
  è alimentato da rete elettrica" (così funziona anche su laptop).
- **Impostazioni**: ✅ "Consenti esecuzione su richiesta" (per testarlo
  a mano), ✅ "Se l'attività non riesce, riavvia ogni: 10 minuti, fino a
  3 volte".

### Verifica

Una volta creato, click destro → **Esegui** sul task. Dopo qualche secondo
controlla `C:\LYSApp\logs\polling.log` per vedere il dettaglio del ciclo:

```powershell
Get-Content C:\LYSApp\logs\polling.log -Tail 40
```

Dovresti vedere righe tipo:

```
2026-05-15 09:00:01 [INFO] polling: === Inizio ciclo polling ===
2026-05-15 09:00:02 [INFO] polling: PEC fetch: scaricati=3 duplicati=0 errori=0
2026-05-15 09:00:05 [INFO] polling: Mail 42: match=header_in_reply_to pratica=789 conf=1.00
2026-05-15 09:00:08 [INFO] polling: Mail 42: categoria=nomina_perito conf=0.95 cost=0.0012 EUR
2026-05-15 09:00:10 [INFO] polling: Notifiche: push=2 email=True errors=0
2026-05-15 09:00:10 [INFO] polling: === Fine ciclo polling ===
```

---

## 6. Firewall LAN

Per permettere ai tablet aziendali di raggiungere `http://<ip-pc>:8000`,
apri la porta 8000 **solo per la rete privata** (NON per quella pubblica).

PowerShell come admin:

```powershell
New-NetFirewallRule -DisplayName "LYS Workflow Hub (LAN)" `
                    -Direction Inbound `
                    -Protocol TCP `
                    -LocalPort 8000 `
                    -Action Allow `
                    -Profile Private
```

> ⚠️ Importante: `-Profile Private` esclude le reti `Public` e `Domain` per
> sicurezza. Se la tua LAN aziendale è classificata come "Pubblica" su Windows,
> riclassificala come "Privata" da Impostazioni → Rete e Internet.

---

## 7. Trovare l'indirizzo per i tablet

Sul PC della carrozzeria, in PowerShell:

```powershell
ipconfig | findstr IPv4
```

Trovi qualcosa come `Indirizzo IPv4 . . . . . . . . . : 192.168.1.42`.

Sui tablet aziendali (connessi alla stessa WiFi) apri il browser su:

```
http://192.168.1.42:8000
```

Aggiungilo ai preferiti / schermata home del tablet.

> 💡 Per evitare che l'IP cambi nel tempo, chiedi al router di assegnare al PC
> della carrozzeria un IP riservato (DHCP reservation). La procedura dipende
> dal modello di router. In alternativa, configura un IP statico sul PC.

---

## 8. Aggiornare l'app

L'aggiornamento gira attorno a uno script `update_lys.bat` che fa tutto da
solo: preserva `.env` e l'ambiente virtuale `.venv`, sposta la vecchia
versione in una cartella di backup datata, mette in piedi la nuova e riavvia
il task.

### 8.1 Prima volta — copia lo script di update nella cartella padre

Subito dopo la prima installazione, fai questo **una sola volta**:

```powershell
copy C:\LYSApp\lys-workflow-hub\scripts\update_lys.bat C:\LYSApp\update_lys.bat
```

Così lo script sopravvive agli aggiornamenti futuri (perché sta in
`C:\LYSApp\`, non dentro la cartella del repo che viene sostituita).

### 8.2 Procedura di aggiornamento

Quando rilascio una versione nuova del codice:

1. **Sul PC sviluppatore:** scarica lo ZIP aggiornato da GitHub.
2. **Trasferisci lo ZIP** sul PC carrozzeria (chiavetta / OneDrive / mail).
3. **Sul PC carrozzeria:** estrai lo ZIP in `C:\LYSApp\` e rinomina la
   cartella estratta in `lys-workflow-hub-new`. Il risultato deve essere:

   ```
   C:\LYSApp\
   ├── lys-workflow-hub\           <- versione attuale, ancora attiva
   ├── lys-workflow-hub-new\       <- versione nuova appena estratta
   └── update_lys.bat
   ```

4. **Doppio click su `C:\LYSApp\update_lys.bat`** (oppure tasto destro →
   Esegui come amministratore se Windows lo chiede).

Lo script:

- ferma il task LYS Workflow Hub,
- sposta `.env` e `.venv\` dalla vecchia versione alla nuova,
- archivia la vecchia versione in `C:\LYSApp\lys-workflow-hub-backup-<data>\`,
- installa eventuali nuove dipendenze Python,
- riavvia il task.

A fine processo l'app riparte con il codice nuovo e tutte le impostazioni
intatte. Se qualcosa non quadra, la cartella di backup è ancora lì e ci
puoi tornare con una `move` manuale.

Se ho rilasciato modifiche allo schema atteso del DB, lo schema-check al boot
te lo dice subito nei log: `Schema check fallito: mancano colonne attese`.

---

## 9. Risoluzione problemi

### L'app non parte all'accensione

- Apri Task Scheduler, trova `LYS Workflow Hub`, guarda la colonna "Ultimo
  esito": `0x0` = OK, altrimenti hai il codice di errore.
- Tasto destro → **Esegui** per provare a lanciarla manualmente: se va così,
  il problema è nei trigger; se non va nemmeno qui, il problema è nell'app o
  nell'ambiente.
- Controlla che la password dell'utente nell'attività di pianificazione sia
  ancora valida (Windows la chiede di nuovo se l'utente cambia password).

### Errore "Schema check fallito"

WinCar è stato aggiornato e una colonna attesa non esiste più o si chiama
diversamente. Apri PowerShell:

```powershell
cd C:\LYSApp\lys-workflow-hub
.venv\Scripts\activate
python scripts\dump_schema_wincar.py
```

Mandami il file `wincar_schema_<data>.txt` generato: in 15 minuti adatto il
mapping e ti spingo un fix con `git pull`.

### Generazione PDF non funziona

Quasi sempre è Word che non risponde al COM in sessione non interattiva. Se hai
seguito Task Scheduler (§5) dovrebbe funzionare. Se hai usato NSSM, verifica:

- Il servizio gira sotto un utente reale (`services.msc` → tab Accesso)?
- Esistono le cartelle:
  - `C:\Windows\System32\config\systemprofile\Desktop`
  - `C:\Windows\SysWOW64\config\systemprofile\Desktop`
  
  Se mancano, creale (vuote). È un workaround noto per Word COM in contesto
  servizio.
- In ultima istanza, lascia che l'app produca il .docx (sempre funzionante) e
  apri/stampa manualmente il file con Word.

### "Address already in use" sulla porta 8000

Un'altra istanza dell'app è già attiva. PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force
```

Oppure scegli un'altra porta in `.env` (`APP_PORT=8001`).

### Tablet non raggiunge il PC

- Verifica firewall (§6).
- Verifica IP (§7): l'IP del PC potrebbe essere cambiato.
- Ping dal tablet: `ping 192.168.1.42` da app terminale (Termius su iPad, ecc.)
  Se il ping non passa, è un problema di rete / firewall, non dell'app.

### Logs dell'app

L'app scrive **automaticamente** i log su file con rotazione, in:

```
C:\LYSApp\logs\lys-hub.log
```

Il file viene ruotato a 5 MB e conserva 5 backup (`lys-hub.log.1`, `.2`, …).
Contiene sia i log dell'app sia quelli di uvicorn (richieste HTTP, errori).
La cartella viene creata automaticamente al primo avvio.

Per leggere in tempo reale cosa fa l'app, da PowerShell:

```powershell
Get-Content C:\LYSApp\logs\lys-hub.log -Wait -Tail 20
```

Se vuoi cambiare path o verbosità, modifica nel `.env`:

```dotenv
APP_LOG_PATH=C:\LYSApp\logs\lys-hub.log
APP_LOG_LEVEL=INFO        # DEBUG / INFO / WARNING / ERROR
```

### "Avvio task ma vedo finestra cmd vuota / non vedo la finestra ma l'app non risponde"

Se vedi la finestra cmd → il task sta usando `python.exe`. Aggiorna
`start_lys.bat` per usare `.venv\Scripts\pythonw.exe` (vedi §5.1).

Se non vedi nessuna finestra ma <http://localhost:8000> non risponde:
apri `C:\LYSApp\logs\lys-hub.log` e cerca tracebacks o errori di config
(es. driver Access non trovato, percorso WinCar errato, porta 8000 occupata).

---

## Riepilogo cartelle dopo l'installazione

```
C:\LYSApp\
├── lys-workflow-hub\         <- repository git, codice
│   ├── .venv\                <- virtualenv (NON committato)
│   ├── .env                  <- config locale (NON committato)
│   ├── src\…
│   ├── start_lys.bat         <- script di avvio
│   └── …
├── Cessioni_firmate\         <- archivio centrale, copia delle scansioni
│   └── 2026\
└── logs\                     <- log dell'app (opzionale)
```

`C:\WinCar\Archivi\` resta dov'è — non lo tocchiamo: l'app lo legge e basta.
