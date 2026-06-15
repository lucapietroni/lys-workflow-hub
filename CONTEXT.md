# LYS Workflow Hub — Contesto di sviluppo

> Branch: **v2** · Versione: **2.0.0-dev** (base: v1.0.3 / main)

---

## Cos'è questo progetto

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl** (Roma).
Legge le pratiche dal gestionale **WinCar** (database Microsoft Access `.mdb`) in
sola lettura, genera documenti precompilati, monitora le risposte assicurative
via PEC/email, classifica con AI (Anthropic Claude), produce bozze di replica,
genera alert SLA. Branch v2 aggiunge i verbali di consegna/riconsegna veicoli
di cortesia.

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API · `pypdf` ·
python-docx + docx2pdf (Word COM)

**Deploy**: `C:\LYSApp\lys-workflow-hub` (Windows, Task Scheduler).
Dev: WSL2 (`/mnt/c/Users/lucap/Documents/Claude/Projects/Lysauto/lys-workflow-hub`).

---

## Architettura

```
Web UI (FastAPI + Jinja2)
    │
    ├── Workflow A — Cessione del credito        → python-docx → PDF via Word COM
    ├── Workflow B — Richiesta vandalismo         → PEC/email SMTP
    ├── Workflow C — Lettura risposte             → IMAP → AI → bozze → SLA
    └── Workflow D — Verbali cortesia [v2]        → python-docx → PDF via Word COM

Script polling (Task Scheduler)
    └── run_polling.py: fetch → match → classify → auto-transition → notify
```

**DB SQLite** tabelle principali:
- `mail_in` — email in arrivo (`ignorata INTEGER DEFAULT 0`)
- `mail_classificate` — risultato AI per ogni mail
- `pec_inviate` — audit log PEC uscenti
- `pratica_stato` — storia stati pratica (append-only)
- `bozze_risposta` — bozze generate per risposta alle compagnie
- `compagnie_assicurative` — anagrafica + PEC + soglie SLA personalizzate
- `categoria_policy` — policy generazione bozze per categoria AI
- `pec_sla_reminder` — tracking escalation SLA già inviati

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py                         Entry point FastAPI
├── config.py                       Caricamento .env (Settings)
├── core/
│   ├── wincar_repository.py        Lettura WinCar (read-only)
│   ├── mail_in_repository.py       Mail in arrivo + classificazioni
│   ├── pratica_stato_repository.py Stato pratica + SLA + escalation
│   ├── pec_log_repository.py       Audit PEC inviate
│   ├── draft_repository.py         Bozze di risposta
│   ├── compagnie_repository.py     Anagrafica compagnie
│   ├── categoria_policy_repository.py  Policy bozze
│   └── sollecito_repository.py     Solleciti SLA
├── integrations/
│   ├── imap_fetcher.py             Fetch IMAP + estrazione body + PDF
│   ├── ai_classifier.py            Classificatore Anthropic Claude
│   ├── pdf_extractor.py            Estrazione testo PDF allegati (pypdf)
│   ├── pec_mailer.py               SMTP + IMAP append posta inviata
│   └── notifier.py                 Push ntfy + email
├── workflows/
│   ├── cessione_credito/           Workflow A (data.py, generator.py, archive.py)
│   │   └── assets/                 Firma pre-apposta (PNG)
│   ├── risarcimento_vandalismo/    Workflow B (data.py, pec_generator.py, invio_pec.py)
│   ├── risposte/                   Workflow C (matcher.py, body_generator.py, ...)
│   └── verbale_cortesia/           Workflow D [v2]
│       ├── data.py                 VerbaleData dataclass + from_pratica()
│       ├── generator.py            DOCX uscita/rientro (logo LYS, tabelle bordate)
│       ├── archive.py              Salva PDF in Pratiche/<n>/Pubblici/Allegati/
│       └── assets/logo_lys.png     Logo LYS Auto Carrozzeria & Noleggio
└── web/
    ├── routes.py                   Pratica + Workflow A
    ├── routes_vandalismo.py        Workflow B
    ├── routes_risposte.py          Cruscotto risposte
    ├── routes_bozze.py             Cruscotto bozze
    ├── routes_verbale.py           Workflow D [v2] — 6 route
    ├── routes_compagnie.py         CRUD compagnie
    ├── routes_impostazioni.py      Statistiche + policy editor
    └── templates/ + static/
scripts/
└── run_polling.py                  Ciclo polling completo
```

---

## Workflow D — Verbali cortesia [v2]

### Flusso utente
1. Pagina pratica → bottone "Verbale uscita/rientro veicolo cortesia"
2. Form pre-compilato da WinCar: nome, CF, indirizzo, CAP, telefono, marca/modello,
   targa, telaio. Campi manuali: patente, km, carburante, accessori, danni (3 righe),
   note, data/ora (auto-fill con datetime corrente, modificabile).
3. "Scarica PDF" → download immediato. "Genera e salva in WinCar" → salva in
   `Pratiche/<n>/Pubblici/Allegati/Verbale_{Uscita|Rientro}_YYYYMMDD.pdf` + redirect.

### Differenze uscita vs rientro
- Uscita: label "Km alla consegna", sezione Franchigie (editabile), titolo "Verbale di Uscita"
- Rientro: label "Km alla riconsegna", no Franchigie, titolo "Verbale di Rientro"
- Generator: funzione `generate(data)` dispatcha su `data.tipo`

### TODO Workflow D
- Sezione danni con UI grafica (schema auto cliccabile con zone cliccabili)
- Franchigie: decidere valori default per auto di cortesia LYS

### Route (routes_verbale.py)
```
GET  /pratiche/{n}/verbale/uscita          Form uscita pre-filled
POST /pratiche/{n}/verbale/uscita/pdf      Genera → download PDF
POST /pratiche/{n}/verbale/uscita/salva    Genera → salva WinCar → redirect
GET  /pratiche/{n}/verbale/rientro         Form rientro pre-filled
POST /pratiche/{n}/verbale/rientro/pdf     Genera → download PDF
POST /pratiche/{n}/verbale/rientro/salva   Genera → salva WinCar → redirect
```

---

## Decisioni tecniche chiave

### PEC InfoCert — struttura messaggi
Le PEC InfoCert hanno struttura a wrapper: il messaggio esterno è il container,
il messaggio reale è l'allegato `postacert.eml` (tipo `message/rfc822`).

**Regola**: in `imap_fetcher.py`, `inner_msg = _find_postacert(msg) or msg` calcolato
**una sola volta** in `fetch_into`, passato sia a `_has_attachments()` sia a
`augment_body_with_pdf()`. Mai usare `msg` diretto per cercare allegati.

### PDF allegati — estrazione sempre
`augment_body_with_pdf()` estrae e appende il testo PDF **sempre**, indipendentemente
dalla lunghezza del body. La vecchia logica con soglia `min_body_len=200` è stata rimossa.

### Classificazione AI — categorie
5 categorie: `presa_in_carico`, `nomina_perito`, `richiesta_documenti`, `liquidazione`, `altro`.

**Regola critica**: `liquidazione` richiede importo concreto o conferma pagamento.
I dinieghi ("veicolo non assicurato") → `altro` con `action_required=True`.
Nel prompt: **"I dinieghi NON sono liquidazioni"**.

### Auto-transizione stato pratica
`auto_transition()` in `pratica_stato_repository.py`:
- `presa_in_carico` → `in_gestione`
- `nomina_perito` → `perito_nominato`
- `liquidazione` → `in_liquidazione`

**Regola**: eseguita solo se `confidence >= 0.70` (`run_polling.py`).

### Soft delete mail_in
`delete_mail()` scrive `UPDATE mail_in SET ignorata=1` (mai DELETE fisico).
`max_uid()` include `ignorata=1` → UID non scende → fetcher non riscarica.

**Regola critica**: `delete_mail()` e `ignora_non_matchate()` NON cancellano
`mail_classificate`. Se cancellate, `mc.id IS NOT NULL` fallirebbe e la mail
uscirebbe dal tab "Da collegare". Badge a 0 perché `count_non_matchate()` filtra
`ignorata=0`.

### Content-Disposition per allegati
**Bug Starlette**: `headers={"Content-Disposition": "inline; ..."}` (mixed case) +
`filename=` produce due header duplicati. Fix: per tipi inline NON passare `filename=`,
usare chiave lowercase `"content-disposition"`. Applicato in `routes_vandalismo.py` e
`routes_bozze.py`.

### Generazione DOCX (Workflow A + D)
python-docx → bytes → docx2pdf (Word COM su Windows) → PDF bytes.
- Workflow A (cessione): `cessione_credito/generator.py` + `pdf_converter.py`
- Workflow D (verbali): `verbale_cortesia/generator.py` — riusa `pdf_converter.py`
  da cessione tramite import diretto nel `__init__.py`

**Path salvataggio entrambi**: `Pratiche/<n>/Pubblici/Allegati/`

### Editable install venv
Il file `.venv/Lib/site-packages/__editable__.lys_workflow_hub-0.1.0.pth`
deve puntare a `C:\Users\lucap\Documents\Claude\Projects\Lysauto\lys-workflow-hub\src`
(non al path OneDrive precedente). Verificare se il venv viene ricreato.

---

## Ambiente

- **Dev**: WSL2, repo `/mnt/c/Users/lucap/Documents/Claude/Projects/Lysauto/lys-workflow-hub`
- **Prod**: `C:\LYSApp\lys-workflow-hub` (Windows), Task Scheduler
- **gh CLI**: `"/mnt/c/Program Files/GitHub CLI/gh.exe"` (non nel PATH WSL)
- **Python env**: `.venv` nella root del repo
- **DB**: `data/lys_hub.db` (gitignored)
- **Chiave API**: in `.env` (gitignored)

---

## Versioni

| Versione | Branch | Contenuto |
|----------|--------|-----------|
| 1.0.3 | main | Base stabile: cessione, vandalismo, risposte AI, bozze, SLA, UI dark glass |
| 2.0.0-dev | v2 | + Verbali consegna/riconsegna veicoli di cortesia (Workflow D) |

---

## TODO v2

- Deploy v2 su prod (dopo test completo su dev)
- Sezione danni verbali: UI grafica schema auto
- Franchigie verbali: definire valori default LYS
- Merge v1 fixes in v2 con `git merge main` quando escono aggiornamenti v1.0.x
