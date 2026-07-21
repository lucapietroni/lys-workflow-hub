# LYS Workflow Hub

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database WinCar in sola lettura,
genera documenti precompilati, monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI e genera alert mirati.

> Branch attivo: **v2** · Versione: **3.0.0**
> Branch stabile: **main** (v1.0.4)

---

## Rami di sviluppo

| Branch | Versione | Stato |
|--------|----------|-------|
| `main` | **1.0.4** | Stabile — funzionalità assicurative complete + allegati email + fix re-download |
| `v2` | **3.0.0** | Sviluppo attivo — verbali cortesia + foto lavorazioni automatiche + foto/documenti in pratica + autenticazione (fase 1) |

---

## Cosa fa v1 (base comune)

- **Cessione del credito**: genera `.docx`/PDF precompilato da dati WinCar.
- **Richiesta risarcimento vandalismo**: bozza PEC con allegati da cartella WinCar, invio via InfoCert + email ordinaria, dual-send, IMAP append.
- **Lettura risposte assicurative**: fetch IMAP incrementale (PEC + email), matching automatico alla pratica, classificazione AI (Claude) in 5 categorie, notifiche push/email.
- **Cruscotto bozze**: generazione automatica bozze di risposta, editor + invio PEC, allegati.
- **Stato pratica + SLA**: ciclo vita pratica, transizioni automatiche da AI, escalation SLA a tre livelli (sollecito / formale / diffida).
- **Statistiche**: KPI globali e per compagnia, costi AI, tempi di risposta.
- **UI dark glass**: tema navy/oro con logo LYS Auto, animazioni, KPI cliccabili.

---

## Novità v3.0 (fase 1) — Autenticazione

Primo passo verso la pubblicazione dell'app su internet (port forwarding dal
router della carrozzeria): fino alla v2.2 chiunque sulla LAN poteva aprire
qualsiasi pagina, senza login.

- **Login/logout**: pagina `/login`, sessione via cookie firmato, tutte le
  route esistenti ora richiedono un utente autenticato (redirect automatico
  a `/login` se assente).
- **Ruoli**: `admin` (accesso completo) ed `esterno` (per ora senza pagine
  dedicate — arriveranno nelle fasi successive: portale di collaborazione
  per agenzie pratiche auto/avvocati esterni).
- **Anti-bruteforce**: blocco account dopo 5 tentativi falliti consecutivi
  (configurabile), sblocco automatico dopo 15 minuti.
- **Bootstrap**: nessuna self-registration — `scripts/create_admin.py` crea
  il primo utente admin dopo il deploy.
- **`SECRET_KEY` obbligatoria in produzione**: l'app non si avvia senza,
  per evitare sessioni firmate con una chiave debole/assente.

**Fase 2 — reverse proxy + TLS**: guida operativa pronta in
`docs/SETUP_PRODUCTION.md` (§10) + config di riferimento in
`deploy/Caddyfile`; esecuzione sul PC/router carrozzeria (DNS, port
forward, Caddy) ancora da fare, richiede accesso fisico al router.

Fasi successive (non ancora costruite): assegnazione pratiche a utenti
esterni, note di collaborazione condivise, calendario per pratica,
notifiche reminder. Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v2.2 — Foto/documenti in pratica + targhe più affidabili

**Riquadri su `/pratiche/<n>`** (sotto "Assicurazione cliente"):
- **Foto pratica**: miniature di tutte le immagini archiviate (`Pubblici/Foto/` +
  eventuali immagini finite in `Pubblici/Allegati/`). Click → ingrandita in overlay
  nella stessa pagina (nessun download, nessuna nuova finestra).
- **Documenti**: elenco di PDF e altri allegati non immagine. Click → apre in una
  nuova scheda del browser col viewer nativo (nessun download forzato).

**Lettura targa più affidabile** (Workflow E):
- Modello vision dedicato (più capace di Haiku su foto di taglio/angolate).
- Prompt riscritto: gestisce targhe capovolte (portellone aperto oltre la verticale),
  disambigua caratteri simili, niente più placeholder letterale che il modello
  a volte ripeteva invece di dire "non leggo nulla".
- **Due passaggi (locate+zoom)**: se la targa è piccola/distante in una foto
  d'insieme, un secondo tentativo mirato ritaglia la zona indicata dal primo
  passaggio a piena risoluzione — aggira il ridimensionamento automatico
  dell'API che altrimenti distrugge il dettaglio.
- Filtro cestino Android sincronizzato per errore da Syncthing.
- Toggle su `/foto` per disattivare la copia automatica nella cartella pratica
  (utile se più pratiche WinCar condividono la stessa targa).

---

## Novità v2.1 — Foto lavorazioni automatiche

Flusso **zero azioni operative**: lo smartphone aziendale (Android) scatta foto
durante le lavorazioni → Syncthing sincronizza in background → LYS Hub rileva,
riconosce la targa e archivia automaticamente.

**Flusso:**
1. Syncthing deposita le foto in `C:\LYSApp\Inbox Foto\`
2. Watchdog (thread daemon) rileva il nuovo file → coda thread-safe
3. Claude Vision estrae la targa dal formato italiano (AA000AA)
4. Foto copiata **sempre** in `C:\LYSApp\Foto lavorazioni\<TARGA>\`
5. Se pratica trovata in WinCar → copiata anche in `Pratiche\<n>\Pubblici\Foto\`
6. Log salvato in SQLite; file eliminato dall'inbox
7. Pagina `/foto` mostra log ultime 100 foto con stato e percorsi

**Setup produzione:**
```
# .env
FOTO_INBOX_PATH=C:\LYSApp\Inbox Foto

# venv
pip install watchdog>=4.0
```
Poi configurare Syncthing: smartphone Android → `C:\LYSApp\Inbox Foto`.

**Formati supportati**: `.jpg` `.jpeg` `.png` `.webp` (Android default è JPEG).
HEIC (iPhone): loggato e saltato — configurare fotocamera su "Compatibilità massima".

---

## Novità v2 — Verbali veicoli di cortesia

Nuovo workflow per generare i verbali di **consegna** e **riconsegna** dei veicoli
di cortesia. Accessibile dalla pagina pratica con due nuovi pulsanti.

**Flusso:**
1. Dropdown seleziona auto di cortesia (gestite in Impostazioni) → targa/marca/telaio
   auto-compilati; km e danni pre-compilati dall'ultimo verbale di rientro per quella auto.
2. Dati locatario pre-compilati da WinCar; campi manuali: patente, carburante, accessori,
   danni (3 righe), note, data/ora (auto-fill modificabile).
3. **Scarica PDF** (download immediato) oppure **Genera e salva in WinCar**
   (salva in `Pratiche/<n>/Pubblici/Allegati/`).

**Verbale Uscita** (2 pagine):
- Pagina 1: locatario, veicolo cortesia, franchigie, danni, note, firme con timbro LYS Auto.
- Pagina 2: Dichiarazione di necessità auto sostitutiva — campi pre-compilati da WinCar
  (assicurazione, polizza, data sinistro, veicolo cliente); motivazione selezionabile.

**Verbale Rientro** (1 pagina): stessa struttura senza franchigie né dichiarazione.

**Auto di cortesia**: CRUD in `/impostazioni` — aggiunge/modifica/rimuove le auto
disponibili (targa, marca/modello, telaio, note). Il verbale successivo pre-carica
km e danni dall'ultimo rientro registrato per quella auto.

Logo LYS Auto + timbro nell'intestazione e nelle firme, nessun riferimento esterno.

---

## Architettura

```
Web UI (FastAPI + Jinja2)
    │
    ├── Workflow A — Cessione del credito        → python-docx → PDF via Word COM
    ├── Workflow B — Richiesta vandalismo         → PEC/email SMTP
    ├── Workflow C — Lettura risposte             → IMAP → AI → bozze → SLA
    ├── Workflow D — Verbali cortesia [v2]        → python-docx → PDF via Word COM
    └── Workflow E — Foto lavorazioni [v2.1]      → watchdog → Claude Vision → file copy

Script polling (Task Scheduler Windows)
    └── run_polling.py: fetch → match → classify → auto-transition → notify

Foto watcher (thread daemon, avviato al boot se FOTO_INBOX_PATH configurato)
    └── Syncthing inbox → targa via Claude Vision → fallback + WinCar Pratiche/
```

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API ·
`pypdf` · python-docx + docx2pdf (Word COM) · watchdog (file system events) ·
bcrypt + sessione cookie (autenticazione, v3.0)

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py
├── config.py
├── core/
│   ├── ...                         Repository SQLite (mail, pratiche, bozze, SLA)
│   ├── foto_lavorazioni_repository.py  Log foto processate [v2.1]
│   └── utenti_repository.py        Utenti + autenticazione (bcrypt, lockout) [v3.0]
├── integrations/
│   ├── ...                         IMAP, SMTP, AI classifier, PDF extractor, notifier
│   └── foto_watcher.py             Watchdog + Claude Vision + routing foto [v2.1]
├── workflows/
│   ├── cessione_credito/           Workflow A
│   ├── risarcimento_vandalismo/    Workflow B
│   ├── risposte/                   Workflow C
│   └── verbale_cortesia/           Workflow D [v2]
│       ├── data.py                 VerbaleData + from_pratica()
│       ├── generator.py            DOCX uscita/rientro con logo LYS
│       ├── archive.py              Salvataggio in WinCar
│       └── assets/logo_lys.png
└── web/
    ├── auth.py                     Sessione, AuthMiddleware, require_admin, CSRF [v3.0]
    ├── routes_auth.py              GET/POST /login, POST /logout [v3.0]
    ├── routes.py                   Pratica + Workflow A (admin-only)
    ├── routes_vandalismo.py        Workflow B (admin-only)
    ├── routes_risposte.py          Workflow C (admin-only)
    ├── routes_bozze.py             Bozze risposta (admin-only)
    ├── routes_verbale.py           Workflow D [v2] (admin-only)
    ├── routes_foto.py              Workflow E — log foto [v2.1] (admin-only)
    ├── routes_compagnie.py         (admin-only)
    ├── routes_impostazioni.py      (admin-only)
    └── templates/ + static/
scripts/
├── run_polling.py
└── create_admin.py                 Bootstrap primo utente admin [v3.0]
```

---

## Requisiti

- **OS**: Windows 10/11 (driver Microsoft Access Windows-only)
- **Python**: 3.11+ 64-bit
- **Microsoft Access Database Engine 2016** Redistributable 64-bit
- **Microsoft Word** (conversione `.docx` → PDF via `docx2pdf` + COM)
- **WinCar** sullo stesso PC

## Installazione

```bash
git clone https://github.com/lucapietroni/lys-workflow-hub.git
cd lys-workflow-hub
git checkout v2          # per il branch v2
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
copy .env.example .env   # compilare percorsi WinCar, credenziali PEC, chiave Claude, SECRET_KEY
python -m lys_workflow_hub.main
python scripts\create_admin.py   # primo avvio: crea l'utente admin
# apri http://localhost:8000 e fai login
```

## Sicurezza

Nessun dato reale nel repository. `.gitignore` esclude `.mdb`, `.env`, `data/`,
cartelle WinCar, documenti generati. Verificare con `git status` prima di ogni push.

Dalla v3.0 l'app richiede login (vedi "Novità v3.0" sopra). `SECRET_KEY` in
`.env` è **obbligatoria** in produzione (`APP_ENV=production`) — l'app non
si avvia senza. Prima di esporre l'app su internet (port forwarding), va
completata anche la fase 2 (reverse proxy + TLS): non pubblicare la porta
dell'app direttamente in chiaro.

## Licenza

Codice proprietario — Carrozzeria LYS Auto srl. Nessuna ridistribuzione senza
autorizzazione esplicita.

## Contatti

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
