# LYS Workflow Hub — Contesto di sviluppo

> Aggiornato automaticamente ad ogni commit. Versione corrente: **0.7.5**

---

## Cos'è questo progetto

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl** (Roma).
Legge le pratiche dal gestionale **WinCar** (database Microsoft Access `.mdb`) in
sola lettura, genera documenti precompilati (cessione del credito, richiesta
risarcimento per atto vandalico), monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI (Anthropic Claude), produce bozze di
replica e genera alert mirati.

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` · InfoCert
Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API · `pypdf` (estrazione testo
PDF allegati) · python-docx (generazione documenti Word)

**Deploy**: Windows PC carrozzeria (`C:\LYSApp\lys-workflow-hub`), avviato come
Task Scheduler. Dev: WSL2 sul portatile di Luca (`/mnt/c/Users/lucap/Documents/...`).

---

## Architettura

```
Web UI (FastAPI + Jinja2)
    │
    ├── Workflow A — Cessione del credito       → python-docx → PDF via Word COM
    ├── Workflow B — Richiesta vandalismo        → PEC SMTP
    └── Workflow C — Lettura risposte            → IMAP fetch → AI classify → bozze
                                                               → stato pratica
                                                               → SLA escalation
Script polling (Task Scheduler Windows)
    └── run_polling.py: fetch → match → classify → auto-transition → notify
```

**DB SQLite** tabelle principali:
- `mail_in` — email in arrivo (con colonna `ignorata INTEGER DEFAULT 0`)
- `mail_classificate` — risultato AI per ogni mail
- `pec_inviate` — audit log PEC uscenti
- `pratica_stato` — storia stati pratica (immutabile, append-only)
- `bozze_risposta` — bozze generate per risposta alle compagnie
- `compagnie_assicurative` — anagrafica compagnie + PEC + soglie SLA
- `categoria_policy` — policy generazione bozze per categoria AI

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py                         Entry point FastAPI
├── config.py                       Caricamento .env (Settings)
├── core/
│   ├── mail_in_repository.py       Mail in arrivo + classificazioni
│   ├── pratica_stato_repository.py Stato pratica + SLA + escalation
│   ├── pec_log_repository.py       Audit PEC inviate
│   ├── draft_repository.py         Bozze di risposta
│   ├── compagnie_repository.py     Anagrafica compagnie
│   └── categoria_policy_repository.py  Policy bozze
├── integrations/
│   ├── imap_fetcher.py             Fetch IMAP + estrazione body + PDF
│   ├── ai_classifier.py            Classificatore Anthropic Claude
│   ├── pdf_extractor.py            Estrazione testo PDF allegati (pypdf)
│   └── notifier.py                 Push ntfy + email
├── workflows/
│   ├── cessione_credito/           Workflow A (data.py, generator.py, cf_parser.py)
│   ├── risarcimento_vandalismo/    Workflow B (data.py, pec_generator.py, invio_pec.py)
│   └── risposte/                   Workflow C (matcher.py, draft_service.py, ...)
└── web/
    ├── routes.py                   Pratica + cessione credito
    ├── routes_vandalismo.py        Workflow B
    ├── routes_risposte.py          Cruscotto risposte
    ├── routes_bozze.py             Cruscotto bozze
    ├── routes_compagnie.py         CRUD compagnie
    ├── routes_impostazioni.py      Statistiche + policy editor
    └── templates/ + static/
scripts/
└── run_polling.py                  Ciclo polling completo
```

---

## Decisioni tecniche chiave

### PEC InfoCert — struttura messaggi
Le PEC InfoCert hanno struttura a wrapper: il messaggio esterno è il container
PEC, il messaggio reale è l'allegato `postacert.eml` (tipo `message/rfc822`).

**Regola**: in `imap_fetcher.py`, chiamare sempre
`inner_msg = _find_postacert(msg) or msg` una sola volta e passare `inner_msg`
sia a `_has_attachments()` sia a `augment_body_with_pdf()`. MAI usare `msg`
direttamente per cercare allegati.

### PDF allegati — estrazione sempre
`augment_body_with_pdf()` estrae e appende il testo PDF **sempre**,
indipendentemente dalla lunghezza del body. La vecchia logica con soglia
`min_body_len=200` è stata rimossa perché le compagnie possono scrivere qualsiasi
cosa nel corpo (anche testo lungo generico) e allegare la risposta reale come PDF.

### Classificazione AI — categorie
5 categorie: `presa_in_carico`, `nomina_perito`, `richiesta_documenti`,
`liquidazione`, `altro`.

**Regola critica nel prompt**: `liquidazione` richiede un importo concreto o
conferma di pagamento. I dinieghi ("veicolo non assicurato con noi", "polizza non
trovata") vanno in `altro` con `action_required=True`. Questa distinzione è
esplicitata con "I dinieghi NON sono liquidazioni" nel system prompt.

### Auto-transizione stato pratica
`auto_transition()` in `pratica_stato_repository.py` mappa:
- `presa_in_carico` → `in_gestione`
- `nomina_perito` → `perito_nominato`
- `liquidazione` → `in_liquidazione`

**Regola**: in `run_polling.py`, la transizione viene eseguita solo se
`classif_obj.confidence >= 0.70`. Sotto soglia: log + skip. Defense-in-depth
per evitare transizioni errate da classificazioni incerte.

### Soft delete mail_in
`delete_mail()` scrive `UPDATE mail_in SET ignorata=1` invece di DELETE fisico.
Il motivo: `max_uid()` include anche le righe `ignorata=1`, così l'UID non scende
mai e il fetcher non riscarica mail già viste. Tutte le query di lista/count
filtrano `WHERE ignorata = 0`.

### Content-Disposition per file allegati
**Bug Starlette**: passare `headers={"Content-Disposition": "inline; ..."}` (mixed
case) + `filename=nome` a `FileResponse` produce due header duplicati perché Python
dict è case-sensitive e `setdefault("content-disposition", "attachment; ...")` aggiunge
una chiave distinta. Chrome usa `attachment` e scarica il file.

**Fix**: per tipi inline (PDF, immagini, txt) NON passare `filename=` e usare
chiave lowercase `"content-disposition"` nel dict headers. Applicato in
`routes_vandalismo.py` e `routes_bozze.py`.

### ParametriInvio frozen — override body
`ParametriInvio` è `@dataclass(frozen=True)`. Per sovrascrivere il body con la
versione editata dall'operatore si usa `dataclasses.replace(params, body=edited_body)`.

---

## Funzionalità per milestone

| Versione | Milestone | Contenuto |
|----------|-----------|-----------|
| 0.1–0.4 | M1–M4.1 | Cessione credito, PEC vandalismo, lettura risposte, bozze risposta |
| 0.5.0 | M5–M5.3 | Stato pratica, SLA, statistiche, policy editor, estrazione PDF |
| 0.6.0 | M6.1 | Escalation SLA automatica (sollecito/formale/diffida) |
| 0.7.0 | M7 | Fix AI dinieghi, fix PDF inner_msg, riclassifica/elimina, PDF preview |
| 0.7.1 | M7.1 | Fix Content-Disposition inline; bump versione footer |
| 0.7.2 | M7.2 | Aggiunti CONTEXT.md (documentazione sviluppo) e hook git commit reminder |
| 0.7.3 | M7.3 | Fix update_lys.bat: preserva lys_hub.db durante aggiornamento produzione |
| 0.7.4 | M7.4 | Fix prefix matching compagnie bidirezionale + label PEC/email |
| 0.7.5 | M7.5 | Fix compagnia_pec override non svuotato su cambio dropdown |

---

## Lavoro svolto in questa sessione (v0.7.2–0.7.4)

### CONTEXT.md + hook commit reminder (v0.7.2)
- Creato `CONTEXT.md` con documentazione architetturale, decisioni tecniche, milestone.
- Aggiunto PostToolUse hook in `.claude/settings.local.json`: dopo ogni `git commit`
  inietta reminder nel contesto Claude per aggiornare CONTEXT.md prima del push.

### Fix update_lys.bat — preserva DB (v0.7.3)
- **Problema**: `scripts/update_lys.bat` preservava `.env` e `.venv` ma non
  `data/lys_hub.db`. Al primo aggiornamento prod si perdevano compagnie, PEC inviate,
  mail classificate.
- **Fix**: aggiunto blocco `copy /Y lys_hub.db OLD→NEW` con `mkdir data` se assente,
  prima del backup dell'installazione precedente.

### Compagnie — PEC o email obbligatoria + dropdown match multipli (v0.7.3)
- **Anagrafica compagnie** (`/compagnie/nuova`, `/compagnie/{id}`):
  - Validazione cambiata da "PEC obbligatoria" a "PEC **o** email ordinaria obbligatoria"
    (`compagnie_repository.py`: `create()` + `update()`).
  - Form: campo PEC non più `required`; hint aggiornati; JS blocca submit se entrambi vuoti.
- **Invio a email ordinaria**: `data.py` → `compagnia_pec` usa `compagnia.pec or compagnia.email`
  come fallback → SMTP invia all'email quando la compagnia non ha PEC.
- **Dropdown match multipli** (`vandalismo_preview.html`):
  - Aggiunto `lookup_all_by_name()` in `compagnie_repository.py`.
  - `_trova_compagnia()` in `routes_vandalismo.py` restituisce `(compagnia, lista_candidati)`.
  - Se match > 1: mostra `<select>` con tutte le opzioni (nome + PEC/email).
  - `compagnia_id` come hidden field → sopravvive al flusso anteprima → conferma → invia.

### Fix prefix matching compagnie + label PEC/email (v0.7.4)
- **Bug**: `lookup_all_by_name` usava match esatto su `nome_norm`. "Unipol" (norm `unipol`) non
  trovava "Unipol Agenzia 39622" (norm `unipol agenzia 39622`) → dropdown non compariva.
- **Fix**: query con prefix matching bidirezionale:
  `nome_norm = ? OR nome_norm LIKE ? || ' %' OR ? LIKE nome_norm || ' %'`
  → trova sia la compagnia madre cercando la figlia, sia viceversa.
- **Label** campo `compagnia_pec` in `vandalismo_preview.html` cambiata in "Indirizzo PEC / email"
  con hint che spiega che l'invio avviene sempre via server PEC (InfoCert gestisce la consegna
  a email ordinaria in modo trasparente).

### Fix compagnia_pec override su cambio dropdown (v0.7.5)
- **Bug**: cambiando compagnia dal dropdown e cliccando "Rigenera", il campo
  "Indirizzo PEC / email" restava valorizzato con l'indirizzo della compagnia
  precedente. Causa: `overrides["compagnia_pec"]` (valore dal form) aveva priorità
  su `from_pratica()` anche quando l'utente aveva scelto una nuova compagnia.
- **Fix** (`routes_vandalismo.py`): se `compagnia_id` è esplicitamente valorizzato
  (scelta dal dropdown), `overrides.pop("compagnia_pec")` prima di chiamare
  `from_pratica()` → il campo viene ricompilato dalla nuova compagnia.

---

## Lavoro svolto in sessioni precedenti (v0.7.0–0.7.1)

### Fix classificazione AI (dinieghi)
- **Problema**: risposta "veicolo non assicurato con noi" classificata come
  `liquidazione` (85% conf) perché la definizione includeva "il diniego di copertura".
  Pratica transitata erroneamente a "In liquidazione".
- **Fix 1** (`ai_classifier.py`): rimossa "diniego di copertura" da `liquidazione`;
  aggiunta in `altro` con esempi espliciti.
- **Fix 2** (`run_polling.py`): soglia confidence 0.70 per `auto_transition()`.

### Fix estrazione PDF allegati PEC (M5.3 follow-up)
- **Problema**: `augment_body_with_pdf` riceveva il wrapper PEC esterno invece di
  `postacert.eml`. PDF mai trovati.
- **Fix**: `inner_msg = _find_postacert(msg) or msg` calcolato una volta in
  `fetch_into`; passato a `_has_attachments` e `augment_body_with_pdf`.
- **Rimossa** logica soglia 200 char: estrazione sempre.
- **Aggiunto** `reextract_body()` pubblico per il bottone "Riclassifica".

### Cruscotto risposte
- Bottone **🔄 Riclassifica**: re-legge `.eml` dal filesystem, aggiorna body_text,
  cancella e rigenera classificazione AI. Flash banner post-riclassificazione.
- Bottone **🗑 Elimina**: soft delete con conferma inline `<details>`.
- **Soft delete** (`ignorata=1`): mail eliminate non riscaricate al ciclo successivo.

### PDF preview allegati vandalismo
- Rimosso modal `<dialog>` + `<embed>` (causava finestra vuota sovrapposta).
- Pulsante 📄 e nome file diventati `<a target="_blank">` — PDF apre in nuova scheda.
- Fix `Content-Disposition: inline` in route `/pratiche/{n}/allegato`.

### Cessione credito per atto vandalico
- Aggiunto checkbox `e_vandalismo` in `CessioneData`.
- `campi_mancanti()`: skippa i 6 campi controparte se `e_vandalismo=True`.
- Generator: PREMESSO differenziato (vandalism text vs RCA text).
- Template: sezioni controparte nascoste via JS toggle + `required` rimosso.
- `_build_overrides()` in `routes.py` gestisce `e_vandalismo` come bool checkbox.

### PEC vandalismo — textarea editabile
- Rimosso `readonly` dalla textarea bozza PEC; aggiunto `name="pec_body"`.
- Body editato fluisce: `conferma` → hidden field → `invia` via `dc_replace()`.
- "Rigenera anteprima" sovrascrive le edits ricostruendo da zero (comportamento corretto).
- Rimossi: voce "Referente pratica" e firma nome dalla chiusura PEC.

---

## Regole / parametri stabiliti

| Regola | Dove | Dettaglio |
|--------|------|-----------|
| `inner_msg` per PDF | `imap_fetcher.py` | Sempre `_find_postacert(msg) or msg` |
| PDF estrazione sempre | `pdf_extractor.py` | Nessuna soglia body length |
| Soglia confidence auto-transition | `run_polling.py` | `>= 0.70` |
| Soft delete mail | `mail_in_repository.py` | `ignorata=1`, mai DELETE fisico |
| Content-Disposition inline | `routes_*.py` | Chiave lowercase, no `filename=` |
| Prompt AI dinieghi | `ai_classifier.py` | "I dinieghi NON sono liquidazioni" |
| `dc_replace` ParametriInvio | `routes_vandalismo.py` | Per override body editato |

---

## Ambiente

- **Dev**: WSL2, repo in `/mnt/c/Users/lucap/Documents/Claude/Projects/Lysauto/lys-workflow-hub`
- **Prod**: `C:\LYSApp\lys-workflow-hub` (Windows), Task Scheduler
- **gh CLI**: installato in `C:\Program Files\GitHub CLI\gh.exe` (non nel PATH WSL)
  → invocare come `"/mnt/c/Program Files/GitHub CLI/gh.exe" ...`
- **Python env**: `.venv` nella root del repo
- **DB**: `data/lys_hub.db` (gitignored)
- **Chiave API Anthropic**: in `.env` (gitignored)

---

## Pending / TODO

- Aggiornamento produzione (`C:\LYSApp\lys-workflow-hub`) a v0.7.3 — da eseguire
  con `scripts/update_lys.bat` (ora preserva `lys_hub.db`).
- Aggiornamento produzione a v0.7.5 — da eseguire con `scripts/update_lys.bat`.
- Feature candidate discusse ma non implementate: timeline pratica, storico
  comunicazioni unificato, filtri cruscotto, notifica push su risposta ricevuta,
  matching ricevute PEC InfoCert, export CSV/Excel, backup DB automatico notturno.
