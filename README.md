# LYS Workflow Hub

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database WinCar in sola lettura,
genera documenti precompilati, monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI e genera alert mirati.

> Branch attivo: **v2** · Versione: **2.0.0-dev**
> Branch stabile: **main** (v1.0.4)

---

## Rami di sviluppo

| Branch | Versione | Stato |
|--------|----------|-------|
| `main` | **1.0.4** | Stabile — funzionalità assicurative complete + allegati email + fix re-download |
| `v2` | **2.0.0-dev** | Sviluppo attivo — verbali cortesia, auto cortesia DB, dichiarazione necessità |

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
