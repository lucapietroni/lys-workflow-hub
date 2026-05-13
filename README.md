# LYS Workflow Hub

Piattaforma di automazione documentale per la **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database di WinCar in sola lettura,
genera documenti precompilati (cessione del credito, richiesta risarcimento per atto
vandalico, ecc.), monitora le risposte delle compagnie assicurative via PEC ed email
ordinaria, classifica le risposte con un modello AI e produce alert mirati.

> Versione attuale: **0.1.0 — Foundation / Workflow A (cessione del credito)**

## Stato del progetto

| Milestone | Contenuto | Stato |
|----|----|----|
| **M1** | Fondazione + Workflow A (Cessione del credito) | in sviluppo |
| **M2** | Workflow B (Richiesta risarcimento vandalismo) | pianificato |
| **M3** | Sottosistema posta + AI + Workflow C (lettura risposte) | pianificato |

Vedi `docs/Analisi_LYS_Workflow_Hub_v2.docx` per il documento di analisi completo
e `docs/Decisioni_finalizzate_v2.docx` per il riepilogo delle decisioni di progetto.

## Architettura in due righe

```
        +-----------------------------+
        |  Web UI (FastAPI + Jinja2)  |
        +-----------------------------+
                     |
        +-----------------------------+
        |  Workflow registry          |
        |   - cessione_credito        |
        |   - risarcimento_vandalismo |
        |   - (futuri)                |
        +-----------------------------+
                     |
   +-----+ +-----+ +-----+ +-----+ +-----+
   | WC  | | Doc | | Mail| | AI  | |Notif|
   +-----+ +-----+ +-----+ +-----+ +-----+
      |       |       |       |       |
   WinCar  Word    IMAP    Claude  Email +
   (.mdb)  +PDF   PEC/SMTP  API   ntfy.sh
```

- **WC** = connettore WinCar (read-only)
- **Doc** = motore documenti (python-docx + LibreOffice)
- **Mail** = lettore posta (IMAP/TLS)
- **AI** = classificatore di risposte assicurative (Claude API)
- **Notif** = dispatcher notifiche (email + push smartphone)

## Requisiti

- **Sistema operativo:** Windows 10 / 11 (il driver Microsoft Access è Windows-only).
- **Python:** 3.11 o superiore (64-bit).
- **Driver Microsoft Access Database Engine 2016 Redistributable** (64-bit) — gratuito,
  scaricabile dal sito Microsoft.
- **LibreOffice** (per la conversione `.docx` → PDF in modalità headless).
- **WinCar** installato sullo stesso PC dove gira l'app (per accesso diretto ai `.mdb`).

## Installazione

```bash
# 1. Clone
git clone https://github.com/<your-username>/lys-workflow-hub.git
cd lys-workflow-hub

# 2. Virtualenv
python -m venv .venv
.venv\Scripts\activate

# 3. Dipendenze
pip install -r requirements.txt

# 4. Configurazione locale (mai committata)
copy .env.example .env
# poi apri .env e compila percorsi WinCar, credenziali PEC, chiave API Claude

# 5. Verifica accesso WinCar
python scripts/dump_schema_wincar.py
# se produce wincar_schema_*.txt senza errori, il driver e l'accesso al DB funzionano

# 6. Avvio app in sviluppo
python -m lys_workflow_hub.main
# apri http://localhost:8000
```

## Struttura del repository

```
lys-workflow-hub/
├── README.md                       Questo file
├── .gitignore                      Esclude dati reali e credenziali
├── .env.example                    Template di configurazione (sicuro)
├── requirements.txt                Dipendenze Python
├── pyproject.toml                  Metadati del pacchetto
├── docs/                           Documenti di analisi e decisioni
│   ├── Analisi_App_Cessione_del_Credito.docx     (v1, storico)
│   ├── Analisi_LYS_Workflow_Hub_v2.docx           (v2, attuale)
│   └── Decisioni_finalizzate_v2.docx              (registro decisioni)
├── scripts/                        Utility one-shot (dump schema, ecc.)
│   └── dump_schema_wincar.py
├── src/
│   └── lys_workflow_hub/           Pacchetto principale
│       ├── __init__.py
│       ├── main.py                 Entry point FastAPI
│       ├── config.py               Caricamento .env, costanti
│       ├── core/
│       │   ├── wincar_repository.py    Lettura .mdb in sola lettura
│       │   └── schema_check.py         Verifica schema al boot
│       ├── workflows/
│       │   ├── cessione_credito/       Workflow A
│       │   └── risarcimento_vandalismo/ Workflow B (pianificato)
│       ├── integrations/           Lettore posta, AI, notifiche
│       └── web/                    Route FastAPI + template Jinja2
├── tests/                          Test unitari
└── data/                           DB SQLite locale, eml_archive (gitignored)
```

## Sicurezza dei dati clienti

Nessun dato di cliente reale deve mai finire nel repository git. Il `.gitignore` è
configurato per escludere automaticamente:

- File `.mdb`, `.accdb` e relativi backup (database WinCar).
- File `wincar_schema*.txt` (dump schema con dati di esempio).
- Cartelle `wincar-sample/`, `WinCar/`, `Archivio/`.
- File `.env` con credenziali.
- Cartella `data/` interna all'app (può contenere log con dati personali).
- Documenti generati (cessioni firmate, lettere, ecc.).

**Prima di ogni `git push` verifica con `git status` che nessun file con dati reali
sia in stage.**

## Licenza

Codice proprietario sviluppato per Carrozzeria LYS Auto srl. Nessuna ridistribuzione
senza autorizzazione esplicita.

## Contatti progetto

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
