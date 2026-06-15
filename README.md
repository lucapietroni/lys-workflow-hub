# LYS Workflow Hub

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database WinCar in sola lettura,
genera documenti precompilati, monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI e genera alert mirati.

> Branch attivo: **v2** · Versione: **2.0.0-dev**
> Branch stabile: **main** (v1.0.3)

---

## Rami di sviluppo

| Branch | Versione | Stato |
|--------|----------|-------|
| `main` | 1.0.x | Stabile — funzionalità assicurative complete |
| `v2` | 2.0.0-dev | Sviluppo attivo — aggiunge verbali veicoli di cortesia |

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

## Novità v2 — Verbali veicoli di cortesia

Nuovo workflow per generare i verbali di **consegna** e **riconsegna** dei veicoli
di cortesia. Accessibile dalla pagina pratica con due nuovi pulsanti.

**Flusso:**
1. Apertura form da pagina pratica → dati cliente/veicolo auto-popolati da WinCar
   (nominativo, CF, indirizzo, CAP, telefono, marca/modello, targa, telaio).
2. Compilazione manuale dei campi mancanti: patente, km, livello carburante,
   accessori, eventuali danni (3 righe), note, data/ora (auto-fill modificabile).
3. **Scarica PDF** (download immediato) oppure **Genera e salva in WinCar**
   (salva in `Pratiche/<n>/Pubblici/Allegati/Verbale_Uscita_YYYYMMDD.pdf`).

**Verbale Uscita**: locatario, veicolo, franchigie (editabili), danni, firme.
**Verbale Rientro**: stessa struttura, km alla riconsegna, senza franchigie.

Logo LYS Auto nell'intestazione, nessun riferimento a fornitori esterni.
Sezione grafica danni (schema auto cliccabile): TODO futuro.

---

## Architettura

```
Web UI (FastAPI + Jinja2)
    │
    ├── Workflow A — Cessione del credito        → python-docx → PDF via Word COM
    ├── Workflow B — Richiesta vandalismo         → PEC/email SMTP
    ├── Workflow C — Lettura risposte             → IMAP → AI → bozze → SLA
    └── Workflow D — Verbali cortesia [v2]        → python-docx → PDF via Word COM

Script polling (Task Scheduler Windows)
    └── run_polling.py: fetch → match → classify → auto-transition → notify
```

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API ·
`pypdf` · python-docx + docx2pdf (Word COM)

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py
├── config.py
├── core/                           Repository SQLite (mail, pratiche, bozze, SLA)
├── integrations/                   IMAP, SMTP, AI classifier, PDF extractor, notifier
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
    ├── routes.py                   Pratica + Workflow A
    ├── routes_vandalismo.py        Workflow B
    ├── routes_risposte.py          Workflow C
    ├── routes_bozze.py             Bozze risposta
    ├── routes_verbale.py           Workflow D [v2]
    ├── routes_compagnie.py
    ├── routes_impostazioni.py
    └── templates/ + static/
scripts/
└── run_polling.py
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
copy .env.example .env   # compilare percorsi WinCar, credenziali PEC, chiave Claude
python -m lys_workflow_hub.main
# apri http://localhost:8000
```

## Sicurezza

Nessun dato reale nel repository. `.gitignore` esclude `.mdb`, `.env`, `data/`,
cartelle WinCar, documenti generati. Verificare con `git status` prima di ogni push.

## Licenza

Codice proprietario — Carrozzeria LYS Auto srl. Nessuna ridistribuzione senza
autorizzazione esplicita.

## Contatti

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
