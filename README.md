# LYS Workflow Hub

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database WinCar in sola lettura,
genera documenti precompilati, monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI e genera alert mirati.

> Branch: **main** · Versione: **4.27.1** · In produzione su `hub.lysauto.it`
> (contabilità gestionale + SDI mergiata; provider SDI di default `fake`)

---

## Cosa fa

**Workflow assicurativi**
- Cessione del credito e richiesta risarcimento vandalismo: documenti/PEC
  precompilati da dati WinCar, invio via InfoCert + email ordinaria.
- Lettura risposte compagnie (IMAP), matching automatico alla pratica,
  classificazione AI (Claude) in categorie, bozze di risposta, escalation SLA
  a tre livelli.
- Verbali di consegna/riconsegna veicoli di cortesia (PDF, gestione parco
  auto di cortesia).

**Foto e documenti**
- Foto lavorazioni: lo smartphone aziendale scatta durante le lavorazioni,
  Syncthing sincronizza, Claude Vision legge la targa e archivia
  automaticamente nella pratica corretta.
- Foto e documenti in pratica: upload/visualizzazione/download da admin ed
  esterni assegnati, generazione automatica delle miniature lette anche da
  WinCar stesso (`.thumb` + `Thumbs.thumb`). L'admin elimina qualunque
  foto (pulizia coerente su disco e nel gestionale); un esterno elimina
  solo foto/documenti caricati da lui stesso, mai quelli di altri.

**Contabilità gestionale e fatturazione elettronica SDI**
- Contabilità **analitica interna**, non fiscale: nessuna partita doppia,
  nessun registro IVA, non sostituisce il software del commercialista. Serve
  a leggere il margine reale per pratica e la spesa per categoria. L'IVA nei
  movimenti è un dato informativo.
- Movimenti (entrate/uscite) classificati per categoria e collegabili a una
  pratica (o a nessuna: affitto, utenze, assicurazioni aziendali). Inserimento
  manuale per stipendi/F24/spese generali. Vista lista filtrabile per
  categoria/periodo/pratica/stato.
- Scheda economica su ogni pratica: entrate collegate, uscite collegate,
  margine, ripartizione per categoria. Visibile solo agli admin, mai nel
  portale esterno.
- Fatturazione elettronica SDI: WinCar genera gli XML delle fatture attive,
  la piattaforma li importa (filtro per anno + cutoff data). Di default le
  attive sono marcate «storico» — già trasmesse da WinCar/commercialista,
  non re-inviate — e servono solo per la contabilità; l'inoltro allo SDI da
  qui è opzionale e manuale (provider dietro interfaccia astratta, candidato
  Openapi). Le fatture passive ricevute dallo SDI generano una riga fattura +
  un movimento proposto da smistare. Ciclo schedulabile (`run_sdi_poll.py`)
  o azioni manuali da `/contabilita/fatture`.
- Coda "fatture da smistare" con assegnazione categoria/pratica ed eventuale
  split su più pratiche; le attive WinCar prendono in automatico la categoria
  "Riparazioni carrozzeria". Dashboard costi/ricavi per categoria e periodo.
- Costi ricorrenti non fatturati (affitto, autolavaggi, …): template con
  cadenza; il ciclo giornaliero genera i movimenti di uscita dei periodi
  scaduti a partire da una data di inizio.

**Collaborazione e accesso esterno**
- Autenticazione con ruoli (`admin`/`esterno`/`supervisore`/`operatore`),
  sessione cookie, anti-bruteforce, gestione utenti via UI. Il supervisore
  vede tutte le pratiche assegnate a chiunque, in sola lettura. L'operatore
  d'officina crea "ingressi" (bozza pratica con documenti scansionati —
  CID, documento identità, libretto, foto danno) prima che la pratica
  esista in WinCar: un admin li vede in coda, crea la pratica in WinCar a
  mano e la collega, spostando i documenti nel posto giusto.
- Portale esterno (`/portale`): agenzie pratiche auto e avvocati vedono solo
  le pratiche assegnate — dettaglio pratica, note e calendario condivisi,
  cambio stato, upload foto/documenti.
- Notifiche in tempo reale (email, push ntfy.sh, push FCM su app Android e
  browser) configurabili self-service da ogni utente esterno, reminder
  automatici per gli appuntamenti di calendario. Reminder ricorrente per le
  notifiche non gestite (nota/evento/stato/upload), sia lato admin che lato
  collaboratore esterno: se nessuno agisce né la silenzia manualmente entro
  24h, la notifica torna finché la pratica non viene aggiornata.
- App Android companion **LYSApp** (wrapper del portale esterno): notifiche
  push native, scatto foto, galleria con zoom/selezione multipla/scarica/
  condividi.

Changelog tecnico completo, decisioni di design e dettagli di ogni
sottosistema in `CONTEXT.md`.

---

## Architettura

```
Web UI (FastAPI + Jinja2)
    │
    ├── Workflow A — Cessione del credito     → python-docx → PDF via Word COM
    ├── Workflow B — Richiesta vandalismo      → PEC/email SMTP
    ├── Workflow C — Lettura risposte          → IMAP → AI → bozze → SLA
    ├── Workflow D — Verbali cortesia          → python-docx → PDF via Word COM
    └── Workflow E — Foto lavorazioni          → watchdog → Claude Vision → file copy

Script polling (Task Scheduler Windows)
    └── run_polling.py: fetch → match → classify → auto-transition → notify
                        → genera movimenti costi ricorrenti

Foto watcher (thread daemon, avviato al boot se FOTO_INBOX_PATH configurato)
    └── Syncthing inbox → targa via Claude Vision → fallback + WinCar Pratiche/

Ciclo SDI (Task Scheduler, branch feature/contabilita-sdi)
    └── run_sdi_poll.py: XML attivi WinCar → SDI · SDI → fatture passive + movimenti proposti

App Android (Capacitor, wrapper del portale esterno)
    └── mobile/ — vedi mobile/README.md
```

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API · `pypdf` ·
python-docx + docx2pdf (Word COM) · watchdog (file system events) ·
bcrypt + sessione cookie · Firebase Cloud Messaging (push app + browser)

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py
├── config.py
├── core/                            Repository SQLite (mail, pratiche, bozze, SLA, utenti, contabilità, ...)
├── integrations/                    IMAP, SMTP, AI classifier, PDF extractor, notifier, foto_watcher, sdi
├── workflows/
│   ├── cessione_credito/            Workflow A
│   ├── risarcimento_vandalismo/     Workflow B
│   ├── risposte/                    Workflow C
│   ├── contabilita/                 Contabilità gestionale + SDI (scheda economica, import fatture, smistamento, report)
│   └── verbale_cortesia/            Workflow D
│       ├── data.py                  VerbaleData + from_pratica()
│       ├── generator.py             DOCX uscita/rientro con logo LYS
│       ├── archive.py               Salvataggio in WinCar
│       └── assets/logo_lys.png
└── web/
    ├── auth.py                      Sessione, AuthMiddleware, require_admin, CSRF
    ├── routes_auth.py               GET/POST /login, POST /logout
    ├── routes_utenti.py             CRUD utenti /utenti (admin-only)
    ├── routes_portale.py            Portale esterno /portale + dettaglio + note/calendario
    ├── routes_operatore.py          Operatore /operatore — crea ingressi officina (documenti pre-pratica)
    ├── routes_ingressi.py           Ingressi officina /ingressi — admin collega a pratica WinCar (admin-only)
    ├── routes.py                    Pratica + Workflow A + assegnazione + note/calendario (admin-only)
    ├── routes_vandalismo.py         Workflow B (admin-only)
    ├── routes_risposte.py           Workflow C (admin-only)
    ├── routes_bozze.py              Bozze risposta (admin-only)
    ├── routes_verbale.py            Workflow D (admin-only)
    ├── routes_foto.py               Workflow E — log foto (admin-only)
    ├── routes_compagnie.py          (admin-only)
    ├── routes_impostazioni.py       (admin-only)
    ├── routes_contabilita.py        Contabilità gestionale + fatture SDI (admin-only)
    └── templates/ + static/
mobile/                              App Android Capacitor (LYSApp) — vedi mobile/README.md
scripts/
├── run_polling.py
├── run_sdi_poll.py                  Ciclo fatturazione SDI (import attive + invio + sync passive)
├── send_event_reminders.py
└── create_admin.py                  Bootstrap primo utente admin
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
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
copy .env.example .env   # compilare percorsi WinCar, credenziali PEC, chiave Claude, SECRET_KEY
python -m lys_workflow_hub.main
python scripts\create_admin.py   # primo avvio: crea l'utente admin
# apri http://localhost:8000 e fai login
```

Guida completa per l'installazione in produzione (Task Scheduler, reverse
proxy/TLS, firewall): `docs/SETUP_PRODUCTION.md`.

## Sicurezza

Nessun dato reale nel repository. `.gitignore` esclude `.mdb`, `.env`, `data/`,
cartelle WinCar, documenti generati. Verificare con `git status` prima di ogni push.

L'app richiede login. `SECRET_KEY` in `.env` è **obbligatoria** in produzione
(`APP_ENV=production`) — l'app non si avvia senza. L'esposizione su internet
passa da Caddy (reverse proxy + TLS): la porta 8000 dell'app non è mai
raggiungibile direttamente dall'esterno, solo 443/80 verso Caddy.

## Licenza

Codice proprietario — Carrozzeria LYS Auto srl. Nessuna ridistribuzione senza
autorizzazione esplicita.

## Contatti

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
