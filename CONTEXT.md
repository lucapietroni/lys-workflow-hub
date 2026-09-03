# LYS Workflow Hub — Contesto di sviluppo

> Branch: **main** · Versione: **4.11.0** · In produzione su `hub.lysauto.it`

---

## Cos'è questo progetto

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl** (Roma).
Legge le pratiche dal gestionale **WinCar** (database Microsoft Access `.mdb`) in
sola lettura, genera documenti precompilati, monitora le risposte assicurative
via PEC/email, classifica con AI (Anthropic Claude), produce bozze di replica,
genera alert SLA. Include anche: verbali di consegna/riconsegna veicoli di
cortesia, foto lavorazioni automatiche via Syncthing + Claude Vision,
autenticazione con ruoli e portale di collaborazione per agenzie pratiche
auto/avvocati esterni, app Android companion (LYSApp) con notifiche push
native, notifiche push anche nel browser del portale.

Riepilogo funzionalità orientato all'utente in `README.md`. Questo file
documenta COME funziona ogni sottosistema (decisioni tecniche, formati,
gotcha) — il changelog per-commit è in `git log`.

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API · `pypdf` ·
python-docx + docx2pdf (Word COM) · watchdog (file system events) ·
Firebase Cloud Messaging (push app Android + browser)

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
    ├── Workflow D — Verbali cortesia             → python-docx → PDF via Word COM
    └── Workflow E — Foto lavorazioni             → watchdog → Claude Vision → file copy

Script polling (Task Scheduler)
    └── run_polling.py: fetch → match → classify → auto-transition → notify

Foto watcher (thread daemon, avviato al boot se FOTO_INBOX_PATH configurato)
    └── Syncthing inbox → targa via Claude Vision → fallback/<TARGA>/ + WinCar Pratiche/

App Android (Capacitor, wrapper del portale esterno /portale)
    └── mobile/ — vedi mobile/README.md
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
- `foto_lavorazioni` — log foto processate dal watcher
- `utenti` — account applicativi: email, password_hash (bcrypt), ruolo (admin/esterno/supervisore)
- `contabilita_categoria` — etichette ricavo/costo (contabilità gestionale, Fase 1)
- `contabilita_movimento` — entrate/uscite, `pratica_id` nullable, `stato` proposto/confermato
- `contabilita_fattura` + `contabilita_fattura_pratica` — specchio fatture SDI + ponte fattura↔pratica (split)

---

## Struttura repository

```
src/lys_workflow_hub/
├── main.py                         Entry point FastAPI + lifespan (watcher start/stop)
├── config.py                       Caricamento .env (Settings)
├── core/
│   ├── wincar_repository.py        Lettura WinCar (read-only)
│   ├── mail_in_repository.py       Mail in arrivo + classificazioni
│   ├── pratica_stato_repository.py Stato pratica + SLA + escalation
│   ├── pec_log_repository.py       Audit PEC inviate
│   ├── draft_repository.py         Bozze di risposta
│   ├── compagnie_repository.py     Anagrafica compagnie
│   ├── categoria_policy_repository.py  Policy bozze
│   ├── sollecito_repository.py     Solleciti SLA
│   ├── foto_lavorazioni_repository.py  Log foto processate
│   └── utenti_repository.py        Utenti + autenticazione (bcrypt, lockout)
├── integrations/
│   ├── imap_fetcher.py             Fetch IMAP + estrazione body + PDF
│   ├── ai_classifier.py            Classificatore Anthropic Claude
│   ├── pdf_extractor.py            Estrazione testo PDF allegati (pypdf)
│   ├── pec_mailer.py               SMTP + IMAP append posta inviata
│   ├── notifier.py                 Push ntfy + email
│   └── foto_watcher.py             Watchdog + Claude Vision + routing foto
├── workflows/
│   ├── cessione_credito/           Workflow A (data.py, generator.py, archive.py)
│   │   └── assets/                 Firma pre-apposta (PNG)
│   ├── risarcimento_vandalismo/    Workflow B (data.py, pec_generator.py, invio_pec.py)
│   ├── risposte/                   Workflow C (matcher.py, body_generator.py, ...)
│   ├── verbale_cortesia/           Workflow D
│   │   ├── data.py                 VerbaleData dataclass + from_pratica()
│   │   ├── generator.py            DOCX uscita/rientro (logo LYS, tabelle bordate)
│   │   ├── archive.py              Salva PDF in Pratiche/<n>/Pubblici/Allegati/
│   │   └── assets/logo_lys.png     Logo LYS Auto Carrozzeria & Noleggio
│   └── contabilita/                Contabilità gestionale + SDI (logica di dominio)
└── web/
    ├── auth.py                     Sessione, AuthMiddleware, require_admin, CSRF
    ├── routes_auth.py              GET/POST /login, POST /logout
    ├── routes.py                   Pratica + Workflow A (admin-only)
    ├── routes_vandalismo.py        Workflow B (admin-only)
    ├── routes_risposte.py          Cruscotto risposte (admin-only)
    ├── routes_bozze.py             Cruscotto bozze (admin-only)
    ├── routes_verbale.py           Workflow D — 6 route (admin-only)
    ├── routes_foto.py              Workflow E — log foto /foto (admin-only)
    ├── routes_compagnie.py         CRUD compagnie (admin-only)
    ├── routes_impostazioni.py      Statistiche + policy editor (admin-only)
    ├── routes_contabilita.py       Movimenti + categorie contabilità (admin-only)
    └── templates/ + static/
scripts/
├── run_polling.py                  Ciclo polling completo
└── create_admin.py                 Bootstrap primo utente admin
```

Repository contabilità in `core/contabilita_categoria_repository.py`,
`core/contabilita_movimento_repository.py`,
`core/contabilita_fattura_repository.py`.

---

## Contabilità gestionale + fatturazione SDI (branch `feature/contabilita-sdi`)

Livello **analitico/gestionale**, NON fiscale: nessuna partita doppia, nessun
registro IVA, nessun bilancio, nessun vincolo dare/avere. Serve a leggere il
margine reale per pratica e la spesa per categoria. Non sostituisce il
software del commercialista. L'IVA nei movimenti (`importo_iva`) è solo
informativa.

**Fase 1 (fatta, v4.22.0)** — modello dati + CRUD:
- `contabilita_categoria` (ricavo/costo, seed iniziale tipico carrozzeria,
  CRUD; una categoria usata da movimenti si disattiva, non si elimina).
- `contabilita_movimento` — entrata/uscita, `categoria_id` e `pratica_id`
  nullable (`pratica_id` = `F_NUMPRA` WinCar, intero sciolto senza FK, come
  nel resto del progetto), `fattura_id` nullable, `origine`
  (manuale / da_fattura_sdi), `stato` (proposto / confermato — i proposti
  da SDI restano fuori dai totali finché non confermati).
- `contabilita_fattura` — specchio di comodo delle fatture SDI (importi
  complessivi, nessun dettaglio per aliquota). Idempotente su `sdi_id` e
  sulla chiave naturale `(tipo, numero, anno, controparte_piva)`.
- `contabilita_fattura_pratica` — ponte fattura↔pratica con
  `importo_assegnato`: 1:1 oppure split su più pratiche.
- UI: `/contabilita/movimenti` (lista filtrabile categoria/tipo/stato/
  pratica/periodo + totali), form manuale, `/contabilita/categorie`.

**Fase 2 (fatta, v4.23.0)** — scheda economica pratica:
- `workflows/contabilita/scheda_economica.py::costruisci_scheda_economica`
  (db_path, pratica_numero) → `SchedaEconomica`: entrate/uscite/margine
  (solo movimenti `confermato`), ripartizione per categoria, conteggio
  movimenti `proposto` a parte, elenco fatture collegate (non sommate nel
  margine, per non contarle due volte).
- Sezione `#economia` in `pratica_detail.html` (route admin
  `/pratiche/{numero}` in `web/routes.py`). **Non** in
  `portale_pratica_detail.html` / `routes_portale.py`.
- Fixture `client_with_mock_repo` in `tests/test_web_routes.py` ora isola
  anche `get_app_settings` su DB temp (prima le route `/pratiche/{numero}`
  scrivevano su `data/lys_hub.db` di sviluppo — vedi nota igiene test 4.21.2).

**Fase 3 (fatta, v4.24.0)** — integrazione SDI:
- `integrations/sdi.py` — interfaccia `SdiClient` (`invia_fattura` /
  `ricevi_fatture` / `ottieni_pdf`). `FakeSdiClient` (default, nessuna rete) +
  `OpenapiSdiClient` (endpoint REST da validare in sandbox — isolati qui).
  `build_sdi_client(settings)`. Config `.env`: `SDI_PROVIDER` (fake|openapi),
  `SDI_API_KEY`, `SDI_BASE_URL`, `SDI_TEST_MODE`, `SDI_PIVA_AZIENDA`
  (14521721002), `SDI_WINCAR_ATTIVE_DIR`, `APP_ARCHIVIO_FATTURE`,
  `SDI_FETCH_SINCE` (2026-01-01), `SDI_INVIO_DISABILITATO`.
- `workflows/contabilita/sdi_import.py` — parser XML FatturaPA minimale
  (numero/data/controparte/importi complessivi, gestisce 1 Body per file,
  note di credito TD04/TD08 → segno movimento invertito). `importa_attive_da_dir`
  (WinCar XML → riga fattura `da_inviare`, idempotente), `invia_attive_pendenti`
  (→ SDI, stato `inviata`, + movimento proposto entrata), `sincronizza_passive`
  (SDI → riga fattura passiva + movimento proposto uscita, senza categoria/pratica).
- `scripts/run_sdi_poll.py` — ciclo singolo, gemello di `run_polling.py`, lock
  file `sdi_poll.lock` dedicato, push ntfy di riepilogo. Task Scheduler 1x/giorno.
  Riusa `PollingLock` / `_setup_logging` da `run_polling.py`.
- UI `/contabilita/fatture` (admin): lista fatture + bottoni "importa attive",
  "invia a SDI", "sincronizza passive". Coda passive non collegate evidenziata
  (UI di smistamento vera = Fase 4).
- Movimenti generati da fattura: `origine='da_fattura_sdi'`, `stato='proposto'`,
  esclusi dal margine finché non confermati. Idempotenti per `fattura_id`.

**Fase 4 (fatta, v4.25.0)** — coda smistamento + reportistica:
- `workflows/contabilita/smistamento.py` — `coda_passive` (fatture con ≥1
  movimento `proposto`), `smista_fattura(fattura_id, categoria_id,
  assegnazioni)`: sostituisce i movimenti `origine='da_fattura_sdi'` (proposti
  o già smistati — NON quelli manuali) con movimenti `confermato`, uno per
  pratica assegnata + uno per il residuo (totale − somma) senza pratica;
  riscrive `contabilita_fattura_pratica`. Valida somma ≤ totale.
- `workflows/contabilita/report.py` — `costruisci_report(db_path, dal, al)` →
  aggregato per categoria (solo movimenti `confermato`), ricavi/costi/margine.
- Routes admin: `/contabilita/fatture/passive/da-collegare` (coda),
  `/contabilita/fatture/{id}/smista` (form split dinamico),
  `/contabilita/report` (dashboard). Link "Report" / "Fatture" / "Categorie"
  nella testata di `/contabilita/movimenti`.
- `movimento_repo`: `fattura_ids_con_proposti`, `riepilogo_per_categoria`,
  `delete_by_fattura(solo_sdi=)`.

Ciclo delle 4 fasi completo. Manca solo: apertura account Openapi +
validazione endpoint reali in sandbox prima di `SDI_PROVIDER=openapi` in prod.

**Fix post code-review (v4.25.1)**:
- `smista_fattura`: la direzione del movimento si ricava dai movimenti
  `origine='da_fattura_sdi'` (a prescindere dallo stato), non dal fallback
  `uscita` — un ri-smistamento di una fattura attiva / nota di credito non
  ribalta più il segno. Dedup delle assegnazioni sulla stessa pratica.
- `parse_fattura_xml`: rifiuta XML con DTD/entità (anti entity-expansion),
  cap 8 MB, warning su lotti multi-body (importato solo il primo).
- `_archivia_xml`: basename-only sul `filename` del provider (anti path
  traversal).
- `ContabilitaFatturaRepository.delete`: rimuove anche i movimenti SDI legati
  e scollega quelli manuali (niente più righe orfane).
- Totali in `/contabilita/movimenti`: solo movimenti confermati, i proposti
  contati a parte in una nota.
- Redirect con query param url-encoded; seed categorie `INSERT OR IGNORE`;
  `run_sdi_poll.py` notifica anche via FCM (come `run_polling.py`).

**Import fatture attive rivisto (v4.25.2)**:
- `importa_attive_da_dir(..., anno, since, come_storico, categoria_id,
  movimento_repo)`: filtra per anno documento + cutoff data
  (`SDI_ATTIVE_IMPORT_SINCE`, default 2026-01-01). Non importa più
  indiscriminatamente tutta la cartella.
- `come_storico=True` (default): stato fattura `storico` → **mai** re-inoltrata
  da `invia_attive_pendenti`. Le attive le trasmette ancora WinCar/il
  commercialista. Crea il movimento di ricavo: `confermato` (nei report) se
  passi una categoria nel form, altrimenti `proposto` da smistare.
- `marca_da_inviare(fattura_repo, id)` + bottone per-riga "Segna da inviare"
  (`storico` → `da_inviare`) per le poche fatture non ancora trasmesse.
- Form import su `/contabilita/fatture` (anno, categoria ricavo, checkbox
  "già inviate").
- `run_sdi_poll.py`: import attive come `storico` (anno corrente + cutoff);
  invio attive automatico SOLO se `SDI_INVIO_ATTIVE_AUTO=true` (default false).

---

## Workflow D — Verbali cortesia

### Flusso utente
1. Pagina pratica → bottone "Verbale uscita / rientro veicolo cortesia"
2. Dropdown seleziona auto di cortesia (da DB `auto_cortesia`) → targa/marca/telaio
   pre-fill automatico; km e danni pre-fill dall'ultimo verbale rientro per quella auto.
3. Dati locatario pre-compilati da WinCar: nome, CF, indirizzo, CAP, telefono.
4. Campi manuali: patente, livello carburante, accessori, danni (3 righe), note, data/ora.
5. **Verbale Uscita**: include pagina 2 — Dichiarazione di necessità auto sostitutiva
   (assicurazione/polizza/data sinistro/veicolo cliente pre-fill da WinCar, motivazione manuale).
6. "Scarica PDF" → download. "Genera e salva in WinCar" → salva in
   `Pratiche/<n>/Pubblici/Allegati/` + log in `verbali_cortesia` + redirect.

### Differenze uscita vs rientro
- Uscita: Franchigie (editabili), pagina 2 dichiarazione necessità
- Rientro: nessuna franchisia, nessuna dichiarazione; km = km alla riconsegna
- Pre-fill km/danni: uscita legge `get_last_rientro(auto_id)`, rientro non pre-fill

### DB auto cortesia (`auto_cortesia_repository.py`)
- `auto_cortesia`: targa (UNIQUE), marca_modello, telaio, note
- `verbali_cortesia`: tipo, auto_id FK, pratica_numero, km, livello_carburante,
  danni_json, note, data_ora
- CRUD in `/impostazioni` → sezione "Auto di cortesia"

### Layout PDF (generator.py)
- Pagina 1: logo 5cm + titolo + 5 tabelle bordate (locatario, veicolo, franchigie,
  danni, note, firme) — tutto in 1 pagina A4
- Tabelle: `TABLE_WIDTH_DXA = 9977` twips, `_section_row()` sfondo `2C3E50` bianco,
  `_col_header_row()` sfondo `D0D0D0`
- Firme: 3 colonne — data/ora | Il Locatario (timbro LYS 5.5cm) | Il Locatore (firma manuale)
- Pagina 2 (solo uscita): logo 4.5cm + titolo scuro + 4 tabelle (intestazione,
  proprietario veicolo, dichiarazione+motivazioni, luogo/data/firma)

### Route (routes_verbale.py)
```
GET  /pratiche/{n}/verbale/uscita          Form uscita pre-filled (autos dropdown)
POST /pratiche/{n}/verbale/uscita/pdf      Genera → download PDF
POST /pratiche/{n}/verbale/uscita/salva    Genera → salva WinCar → redirect
GET  /pratiche/{n}/verbale/rientro         Form rientro pre-filled (autos dropdown)
POST /pratiche/{n}/verbale/rientro/pdf     Genera → download PDF
POST /pratiche/{n}/verbale/rientro/salva   Genera → salva WinCar → redirect
```

### Allegati email visibili in /risposte/{id} (portato da main v1.0.4)
`list_attachments()` / `get_attachment()` in `imap_fetcher.py` estraggono allegati
dall'inner `postacert.eml`. Route `GET /risposte/{id}/allegati/{i}` serve inline.
Template lista allegati con nome/tipo/dimensione e link "Apri" (nuova scheda).

---

## Workflow E — Foto lavorazioni

### Flusso automatico
1. Syncthing deposita foto da smartphone Android in `foto_inbox_path`
2. `_QueueingHandler` (watchdog) intercetta `on_created` + `on_moved` → coda (`maxsize=500`)
3. Worker thread (`foto-worker`, daemon): file `.trashed-*` (cestino Android
   sincronizzato per errore) scartati subito, senza chiamata AI
4. Claude Vision estrae targa (vedi "Lettura targa a due passaggi" sotto)
5. Copia **sempre** in `foto_fallback_path/<TARGA>/` (o `/SCONOSCIUTA/`)
6. Se pratica trovata in WinCar **e** `copia_pratica_abilitata` (toggle su `/foto`)
   → copia anche in `Pratiche/<n>/Pubblici/Foto/`
7. Log in `foto_lavorazioni` (DB) **prima** dell'unlink
8. File eliminato dall'inbox

### Lettura targa a due passaggi (locate+zoom, v2.2)
L'API Anthropic ridimensiona sempre le immagini a un lato lungo ~1568px prima
di processarle. Su foto d'insieme (vano motore/bagagliaio) con targa piccola,
distante o fisicamente capovolta (portellone aperto oltre la verticale), il
dettaglio va perso nel resize anche partendo da un originale ad alta
risoluzione — il modello finiva per indovinare l'intera targa, non solo
confondere caratteri simili.

1. **1° tentativo** su foto intera (`_PROMPT_LETTURA`, modello
   `anthropic_vision_model`, non più lo stesso Haiku del classificatore email).
   Risposta a tre righe: `RAGIONAMENTO:` / `TARGA:` / `REGIONE:` (bounding box
   % `x1,y1,x2,y2`, best-guess anche se la targa non è letta con certezza).
2. Se `TARGA:` è `NONE` (o in blacklist) ma c'è una `REGIONE:` valida →
   ritaglio quella zona + 8% padding dall'originale a piena risoluzione
   (Pillow, upscale se il crop è sotto 700px), **2° tentativo** mirato
   (`_PROMPT_ZOOM`) solo su quel ritaglio.
3. `_TARGA_BLACKLIST` = targhe mai emesse (`AA000AA`, ecc.): rete di sicurezza
   contro l'allucinazione "placeholder" — il modello a volte ripeteva
   l'esempio di formato del prompt invece di dire NONE. Il prompt v2.2 non
   contiene più un placeholder letterale.

Seconda chiamata AI solo sui casi difficili; foto leggibili al primo
tentativo restano a una sola chiamata (nessun impatto su costo/latenza per
il caso comune).

### Config
```
FOTO_INBOX_PATH=          # vuoto = watcher off (dev); impostare in prod
FOTO_FALLBACK_PATH=C:\LYSApp\Foto lavorazioni
ANTHROPIC_VISION_MODEL=claude-sonnet-5   # separato da ANTHROPIC_MODEL (classificatore email)
```
Watcher avviato in `lifespan()` solo se `foto_inbox_path` non è None.
Toggle `copia_pratica_abilitata` in tabella `foto_settings` (riga singola,
default 1), gestito da `/foto` (bottone) — quando disattivo, `_process()`
salta la ricerca WinCar e la copia in pratica, la foto resta comunque sempre
in `foto_fallback_path/<TARGA>/`.

### Stato record
| stato | significato |
|-------|-------------|
| `ok` | targa estratta + pratica trovata |
| `ok_no_pratica` | targa estratta, nessuna pratica WinCar |
| `targa_non_trovata` | Claude Vision → NONE o risposta non valida |
| `errore` | eccezione (file troppo grande, OS error, Vision timeout) |
| `heic` | formato HEIC non supportato |

### Decisioni tecniche critiche
- **Syncthing `on_moved`**: Syncthing scrive temp file → rinomina atomico → catturare `dest_path` da `on_moved`, non solo `on_created`.
- **OOM guard**: `_MAX_IMG_BYTES = 20 MB` — controllo `stat().st_size` prima di `read_bytes()`.
- **Client AI singleton**: `anthropic.Anthropic(timeout=30.0)` creato in `__init__`, chiuso in `stop()` — un solo httpx pool per tutta la vita del watcher.
- **Crash recovery**: `start()` scansiona inbox e accoda file pre-esistenti — idempotente al riavvio.
- **Filename collision-safe**: `_dest_name()` = `{timestamp}_{uuid6}_{nome_originale}`.
- **Log prima dell'unlink**: se `path.unlink()` fallisce, il record esiste già nel DB.
- **Singleton `app.state.foto_repo`**: creato in `lifespan`, condiviso con `routes_foto.py` — evita DDL per ogni request HTTP.
- **`_WATCHDOG_OK` flag**: import watchdog con fallback a stub classes — app si avvia anche senza watchdog installato; `start()` fallisce con errore chiaro.
- **`_PIL_OK` flag** (v2.2): stesso pattern per Pillow — senza, il modulo resta importabile ma il retry locate+zoom viene saltato (solo 1° tentativo).
- **`created_at` esplicito in Python** (v2.2): `datetime.now().isoformat(...)` passato in ogni `INSERT`, non default SQLite `datetime('now')` (che è UTC — mostrava orari sfasati di 2h rispetto all'Italia CEST).

### Route
```
GET  /foto                    Lista inbox corrente + log ultime 100 foto processate
POST /foto/copia-pratica      Toggle copia_pratica_abilitata
```

---

## Foto e documenti in pratica

Su `/pratiche/<n>`, sotto "Assicurazione cliente": riquadro **Foto pratica**
(miniature) e riquadro **Documenti** (elenco). Riusa `pratica_files.scan()`
(già esistente, usato anche da Workflow B) senza modificarlo.

- `foto_pratica` = `scan().foto` (immagini in `Pubblici/Foto/` + eventuali
  immagini finite in `Pubblici/Allegati/`)
- `documenti_pratica` = `scan().cessioni + denunce + altri` (tutto il resto:
  PDF e altri file non immagine)

**Anteprima inline**: `GET /pratiche/{numero}/file?path=...` — ri-esegue
`scan()` server-side e accetta solo un `path` che corrisponde esattamente a
uno dei file trovati (stesso pattern di sicurezza di `bozza_allegato_preview`
in `routes_bozze.py`: niente path traversal, niente file fuori dalle
cartelle WinCar della pratica). Solo le estensioni in `_PREVIEW_MIME`
(immagini standard + PDF) rispondono con `Content-Disposition: inline`
(chiave header minuscola, niente `filename=` separato — vedi nota Starlette
sotto) → il browser renderizza invece di scaricare. Le altre estensioni
(`.docx`, `.xlsx`, HEIC, ecc.) restano `attachment` col comportamento
`FileResponse` di default — stesso ramo whitelist/fallback di
`bozza_allegato_preview`.

- **Foto**: click su miniatura apre un lightbox JS (overlay nella stessa
  pagina, `<img>` con `src` aggiornato dinamicamente) — nessun download,
  nessuna nuova finestra.
- **Documenti**: link `target="_blank"` — nuova scheda con viewer nativo del
  browser (PDF), nessun download forzato.
- **HEIC/HEIF (iPhone)**: nessun browser le renderizza in `<img>` → escluse
  dalla griglia foto (thumbnail altrimenti rotta e silenziosa), mostrate
  invece come documento (link diretto, che scarica: comportamento corretto
  dato che il browser non può comunque visualizzarle inline).
- **Miniature**: nessun resize server-side, `<img>` full-res mostrata piccola
  via CSS (`object-fit: cover`, griglia `auto-fill`) — accettabile al volume
  tipico (poche/dozzina foto per pratica); da rivedere se in futuro le
  pratiche accumulano decine di foto ad alta risoluzione. Ogni thumbnail
  ri-esegue `scan()` lato server (N+1): accettabile a questo volume, da
  rivedere se cresce molto.

---

## Autenticazione

Prerequisito per pubblicare l'app su internet (port forwarding dal router
della carrozzeria): fino alla v2.2 l'app non aveva alcun login, chiunque
sulla LAN poteva aprire qualsiasi pagina. La v3.0 introduce utenti/ruoli in
più fasi; questa sezione copre la fase 1 (fondamenta auth), già completata.
Fase 2 (reverse proxy + TLS) **completata** — app raggiungibile da internet
su `https://hub.lysauto.it` (CNAME verso il DDNS del router officina,
`lysauto.dnsitalia.org`; dominio `lysauto.it` resta sulla VM separata del
sito, non toccato). Setup: Caddy su `C:\LYSApp\caddy\` (NSSM, auto-start),
reverse proxy verso `127.0.0.1:8000`, cert Let's Encrypt automatico.
Procedura in `docs/SETUP_PRODUCTION.md` §10 + `deploy/Caddyfile`.

**Gotcha reale incontrato in produzione**: la scheda LAN del PC carrozzeria
risultava classificata `NetworkCategory=Public` da Windows (non `Private`
come assunto), quindi le regole firewall `-Profile Private` per le porte
80/443 non si applicavano — porta chiusa dall'esterno nonostante DNS e
router corretti. Fix applicato: **non** riclassificare l'interfaccia
(avrebbe esposto anche altre regole `Private` — condivisione file, network
discovery — sulla LAN), ma scopare le due regole Caddy a `-Profile Any`
(`Set-NetFirewallRule -DisplayName "LYS Workflow Hub (Caddy HTTP/HTTPS)"
-Profile Any`) — chirurgico, indipendente da come Windows classifica la
scheda oggi o in futuro. Sul PC risultava anche un server WireGuard
installato: non c'entrava (la sua regola ha già scope proprio, indipendente
dalla classificazione della scheda LAN), ma vale la pena ricontrollare
`Get-NetConnectionProfile` dopo qualsiasi installazione software di rete.
Fase 3 (assegnazione pratiche) **completata** — vedi sezione dedicata più
sotto. Fasi successive (non ancora costruite): note di collaborazione
condivise, calendario per pratica, notifiche di reminder.

### Modello utenti
Tabella `utenti` (`core/utenti_repository.py`): email UNIQUE, `password_hash`
(bcrypt), `ruolo` (`admin` | `esterno` | `supervisore`), `attivo`,
`failed_login_count` + `locked_until` per il blocco anti-bruteforce. Tre
ruoli fissi per ora (non tabella permessi granulare) — se in futuro serve
più granularità si aggiunge senza toccare lo schema base (`ruolo` è testo
libero lato DB, validato solo in Python contro `RUOLI`).

`supervisore` (aggiunto dopo, vedi sezione "Portale esterno" più sotto):
stesso portale dell'esterno ma vede TUTTE le pratiche assegnate a
qualunque utente (non solo le proprie) e in sola lettura — nessuna route
di scrittura in `routes_portale.py` accetta un suo POST
(`_richiedi_permesso_scrittura`).

### Sessione e protezione route
- `SessionMiddleware` (Starlette, cookie firmato con `SECRET_KEY`) +
  `AuthMiddleware` custom (`web/auth.py`) che carica l'utente dalla sessione
  in `request.state.current_user` a ogni richiesta.
- **Fail-closed**: qualunque path non in `PUBLIC_PATHS`/`PUBLIC_PREFIXES`
  (`/login`, `/health`, `/static/*`) redirige a `/login` se non loggato. Una
  route nuova aggiunta in futuro è protetta di default, non serve ricordarsi
  di aggiungerla a una allowlist.
- `require_admin` (dependency FastAPI) applicato a `dependencies=[...]` di
  **tutti** i router esistenti (`routes.py`, `routes_vandalismo.py`,
  `routes_risposte.py`, `routes_bozze.py`, `routes_verbale.py`,
  `routes_foto.py`, `routes_compagnie.py`, `routes_impostazioni.py`,
  `routes_pec_log.py`, `api.py`) — oggi equivalgono a "richiede login" dato
  che esistono solo admin, ma prepara il terreno: quando arriverà il portale
  per utenti "esterno" (fase 3), quelle route resteranno riservate agli
  operatori carrozzeria e il portale vivrà in router separati senza
  `require_admin`.
- `current_user` disponibile in ogni template via `context_processors=
  [template_context_processor]` passato a **ogni** `Jinja2Templates(...)`
  esistente (una per router file) — necessario perché `base.html` mostra
  nome utente + bottone "Esci" nella topbar.

### CSRF e anti-bruteforce
- Token CSRF legato alla sessione, verificato sul form di login (unico form
  raggiungibile da utente non ancora autenticato). Estensione ai form delle
  pagine già esistenti (compagnie, bozze, verbali, ecc.) è lavoro di
  hardening rimandato, non ancora fatto.
- `authenticate()` in `utenti_repository.py`: dopo `login_max_attempts`
  (default 5) tentativi falliti consecutivi, account bloccato per
  `login_lockout_minutes` (default 15). Messaggio di errore identico per
  "email inesistente" e "password sbagliata" (niente enumerazione utenti);
  hash bcrypt "a vuoto" anche quando l'email non esiste, per non rivelare
  l'esistenza dell'account via timing.

### SECRET_KEY
`config.py` + `main.py` (`_resolve_secret_key()`): se `APP_ENV=production` e
`SECRET_KEY` non è impostata in `.env`, l'app si rifiuta di partire
(`sys.exit(2)`, stesso pattern dello schema check WinCar). In sviluppo, se
vuota, viene generata una chiave effimera ad ogni avvio (sessioni non
sopravvivono al riavvio — comodo per non dover configurare nulla in dev).

### Bootstrap primo admin
Nessuna self-registration. `scripts/create_admin.py` crea (o promuove a
admin resettando la password) un utente via CLI interattiva
(`getpass`, password non echeggiata) o non interattiva (`--email --nome
--password --yes`) — necessario **una sola volta**, al primo deploy
(problema dell'uovo e della gallina: serve un admin per accedere alla UI
`/utenti` che gestisce tutti gli altri account). Da lì in poi si usa la UI.

### Route (routes_auth.py)
```
GET  /login     Form di accesso (pubblico)
POST /login     Verifica credenziali (rate-limited via lockout), apre sessione
POST /logout    Chiude la sessione
```

---

## Assegnazione pratiche

Decide sempre l'admin chi assegnare (mai self-service per l'esterno).
Relazione **many-to-many**: una pratica può avere più collaboratori esterni
insieme (es. agenzia pratiche auto E avvocato sulla stessa pratica).

### Gestione utenti — UI (`routes_utenti.py`, admin-only)
```
GET  /utenti                    Lista (nome, email, ruolo, stato, ultimo accesso)
GET  /utenti/nuovo              Form creazione
POST /utenti/nuovo              Crea (email, nome, ruolo, password ≥8 caratteri)
GET  /utenti/{id}                Form modifica
POST /utenti/{id}                Aggiorna nome/ruolo/attivo/password (password vuota = invariata)
POST /utenti/{id}/elimina        Hard delete
```
**Guard "ultimo admin"**: `UtentiRepository.count_admin_attivi()` blocca
(400, non silenzioso) la disattivazione/retrocessione/eliminazione
dell'ultimo admin attivo rimasto — altrimenti nessuno potrebbe più entrare
per rimediare. Email non modificabile dopo la creazione (readonly nel form)
— evita di dover gestire history/uniqueness in corsa con la sessione attiva.

### Tabella `pratica_assegnazioni` (`core/pratica_assegnazioni_repository.py`)
`pratica_numero`, `utente_id`, `assegnato_da`, `assegnato_at`. Indice
UNIQUE su `(pratica_numero, utente_id)` → `assegna()` è idempotente
(`INSERT OR IGNORE`), niente errore se l'admin clicca "Assegna" due volte.

### UI su `pratica_detail.html`
Card "Collaboratori esterni" (ancora `#collaboratori`, usata dal redirect
post-azione): lista dei collaboratori assegnati con bottone "Rimuovi" per
ciascuno, più un form "Assegna a" con dropdown degli utenti esterni attivi
**non già assegnati** a quella pratica (calcolato server-side,
`esterni_disponibili` nel context di `routes.py:pratica_detail`).

### Portale esterno (`routes_portale.py`, `/portale`)
**Unico router del progetto senza `dependencies=[Depends(require_admin)]`**
— eccezione voluta (vedi commento in testa al file). Protetto comunque da
`AuthMiddleware` (serve essere loggati); la query è filtrata per
`current_user.id` lato server, quindi un admin che apre `/portale` vede
semplicemente una lista vuota (normalmente non gli è assegnato nulla) — non
serve un guard esplicito sul ruolo. Naviga per numero pratica assegnato →
`WinCarRepository.get_pratica(numero)` per il riepilogo (N chiamate, N
piccolo per il volume atteso — stesso compromesso già accettato altrove nel
progetto, es. thumbnail foto in §"Foto e documenti in pratica").
La lista è ora solo un indice: cliccando il numero pratica si apre
`/portale/pratiche/{numero}` (fase 4, vedi sezione dedicata) con la vista
completa — foto, documenti, note e calendario condivisi.

### Nav condizionale per ruolo (`base.html`)
Il link "brand" (logo) e le voci di navigazione cambiano in base a
`current_user.is_admin`: admin vede tutta la nav esistente + "Utenti";
esterno vede solo "Le mie pratiche" (→ `/portale`). Evita di mostrare link
che darebbero comunque 403 a un utente esterno.

### Pattern riusato: repository come singleton su `app.state`
Stesso principio già stabilito per `utenti_repo` in fase 1 (vedi sopra) —
`get_utenti_repo` in `routes_utenti.py` legge da
`request.app.state.utenti_repo`, **non** costruisce una connessione propria
da `Settings`. Lo stesso vale per l'uso di `utenti_repo` dentro
`pratica_detail` (`routes.py`). Motivo: i test devono poter sostituire il
repository con un DB temporaneo senza toccare la cache di `get_settings()`
— un `UtentiRepository` costruito al volo da `Depends(get_settings)`
punterebbe silenziosamente al DB reale anche durante i test, con risultati
falsati (bug reale trovato e corretto durante l'implementazione di questa
fase, stesso pattern di quello preso dal code-reviewer in fase 1).

---

## Note e calendario condivisi

Thread di note e calendario **condivisi** tra admin e collaboratori esterni
sulla stessa pratica — non un canale separato per utente. Sia l'admin
(`/pratiche/{numero}`) sia l'esterno assegnato (`/portale/pratiche/{numero}`)
leggono e scrivono sulle stesse due tabelle.

### Tabella `pratica_note` (`core/pratica_note_repository.py`)
`pratica_numero`, `utente_id`, `autore_nome`, `testo`, `created_at`.
`autore_nome` è uno **snapshot** del nome al momento dell'invio (non un JOIN
live su `utenti`): la nota resta leggibile anche se l'utente viene poi
rinominato o disattivato. Nessun delete: log immutabile, in linea con l'uso
previsto (thread di collaborazione, non editor di testo).

### Tabella `pratica_eventi` (`core/pratica_eventi_repository.py`)
`pratica_numero`, `titolo`, `data_evento`, `creato_da`, `creato_da_nome`,
`created_at`. Calendario leggero (niente ricorrenza, niente notifiche —
quello è fase 5): chiunque abbia accesso alla pratica può aggiungere o
eliminare un evento, non solo chi lo ha creato.

**IDOR prevenuto in `delete()`**: la firma è
`delete(evento_id, pratica_numero)`, non solo `delete(evento_id)` — altrimenti
un esterno assegnato alla pratica A potrebbe cancellare un evento della
pratica B semplicemente indovinando/incrementando l'id, dato che l'unico
controllo lato route è "l'utente ha accesso a *questa* pratica nell'URL",
non "l'evento richiesto appartiene a questa pratica". Coperto da test
(`test_pratica_collaborazione_ui.py::test_portale_non_puo_eliminare_evento_di_altra_pratica`).

### Route
Admin (`routes.py`, dentro il router `require_admin`):
```
POST /pratiche/{numero}/note                     Aggiungi nota
POST /pratiche/{numero}/eventi                    Aggiungi evento
POST /pratiche/{numero}/eventi/{id}/elimina       Elimina evento
```
Esterno (`routes_portale.py`, nessun `require_admin`):
```
GET  /portale/pratiche/{numero}                   Dettaglio (sola lettura WinCar + note/calendario)
GET  /portale/pratiche/{numero}/file              Anteprima foto/documento (verifica assegnazione)
POST /portale/pratiche/{numero}/note              Aggiungi nota
POST /portale/pratiche/{numero}/eventi            Aggiungi evento
POST /portale/pratiche/{numero}/eventi/{id}/elimina  Elimina evento
```
**Bug reale trovato dal code-reviewer prima del commit**: la prima versione
riusava `_allegati_con_url()` di `routes.py` così com'era, che genera URL
fissi su `/pratiche/{numero}/file` — route admin-only. Un utente esterno
apriva `/portale/pratiche/{numero}` e vedeva la pagina, ma ogni foto/doc
rispondeva 403 (middleware `require_admin` sulla route sbagliata), rompendo
proprio la funzionalità che questa fase doveva aggiungere. Non intercettato
dai primi test perché usavano un `wincar_repo` mockato senza file reali su
disco. Fix: `_allegati_con_url()` ha ora un parametro `base` (default
`/pratiche`, portale passa `/portale/pratiche`); la logica di risoluzione
file è stata estratta in `resolve_pratica_file()` (pubblica, non più
annidata nella route) così `routes_portale.py` la riusa con
`_verifica_accesso()` al posto di `require_admin`. Test di regressione con
un file reale su disco:
`test_pratica_collaborazione_ui.py::test_portale_foto_pratica_serve_file_reale_non_403`.
`_verifica_accesso()` in `routes_portale.py` fa 404 (non 403 — non
riveliamo che la pratica esiste) se l'utente esterno non è assegnatario;
un admin che apre `/portale/pratiche/{numero}` passa sempre (comodo per
verificare cosa vede un collaboratore).

### Settings dedicate (`get_portale_settings`)
`routes_portale.py` ora ha un proprio wrapper `get_portale_settings()`
(stesso ruolo di `get_app_settings` in `routes.py`): le route di
note/calendario/scan-allegati lo usano per costruire `PraticaNoteRepository`,
`PraticaEventiRepository` e per `scan_allegati(settings.wincar_archivio, …)`.
Serve per poter sovrascrivere le Settings nei test di questo router senza
toccare il `get_settings()` globale usato altrove (es. da
`get_assegnazioni_repo`/`get_wincar_repo`, che i test bypassano già a un
livello più alto).

---

## Notifiche di collaborazione + prossimi appuntamenti

Due pezzi, entrambi **live** (nessun job schedulato — vedi nota sotto sulla
parte B, non ancora costruita):

**A) Notifiche in tempo reale**, triggerate dalle stesse route POST di nota/
evento di fase 4, subito dopo il salvataggio:
- Esterno scrive nota/evento su una pratica → **push all'admin** (ntfy.sh,
  stesso canale/topic già usato per gli alert PEC in `run_polling.py`).
- Admin scrive nota/evento su una pratica con collaboratori assegnati →
  **email a ciascun esterno assegnato attivo** (stesso SMTP di
  `notify_batch`).
- Scope deliberatamente limitato a nota/evento (non copre cambio stato,
  nuovi documenti WinCar, ecc. — deciso con l'utente prima di implementare).

**B) Widget "Prossimi appuntamenti"**: card su home (admin, tutte le
pratiche) e su `/portale` (esterno, solo pratiche assegnate — filtrate via
`PraticaEventiRepository.list_prossimi(pratica_numeri=...)`), eventi nei
prossimi 7 giorni. Calcolato al caricamento pagina, stesso pattern del
banner SLA già esistente in home — non serve alcuno scheduler.

### `integrations/notifier.py` — funzioni pubbliche
`send_push`/`send_email` erano `_send_push`/`_send_summary_email` (private,
usate solo da `notify_batch`): rinominate pubbliche perché ora servono anche
a `notify_push_nuova_attivita`/`notify_esterno_nuova_attivita`, le due
funzioni "fire-and-forget" chiamate dalle route — **non sollevano mai**
(try/except a monte + logging), perché una nota/evento è già salvato quando
girano: un errore SMTP/ntfy non deve mai far fallire la richiesta HTTP che
li ha innescati.

### `Settings.public_url()` (nuovo campo `public_base_url`)
I link nelle notifiche (click push, link nell'email) puntavano prima a
`http://APP_HOST:APP_PORT` — corretto solo da dentro la LAN. Da quando
l'app è pubblicata (fase 2, `hub.lysauto.it`), un push toccato dal telefono
fuori casa o un'email a un esterno con quel link non avrebbero funzionato.
`public_base_url` (env `PUBLIC_BASE_URL`) risolve l'URL corretto; vuoto =
comportamento precedente (fallback LAN). `scripts/run_polling.py` è stato
aggiornato per usare lo stesso helper (stesso problema, mai notato prima
perché quei link erano cliccati solo da dentro la LAN finora).

**v3.7.2**: il fallback (`PUBLIC_BASE_URL` non impostato) usava `app_host`
letterale — ma `app_host` in produzione è `0.0.0.0` (bind su tutte le
interfacce, §10.7), un indirizzo valido per *ascoltare*, non per
*navigare*: un tap su una notifica push produceva `http://0.0.0.0:8000/...`,
un link che nessun browser sa aprire. Bug reale segnalato in produzione
(prima ancora di impostare `PUBLIC_BASE_URL` sul PC carrozzeria). Fix:
`app_host == "0.0.0.0"` → usa `localhost` nel fallback. **La soluzione
vera resta impostare `PUBLIC_BASE_URL=https://hub.lysauto.it` in prod
`.env`** — il fallback a `localhost` è solo difesa in profondità, funziona
comunque solo da dentro la LAN (e nemmeno da lì se non sei sullo stesso PC).

## Preferenze di notifica self-service

Motivazione: parte A manda **sempre** email all'esterno assegnato e **sempre**
push all'admin sullo stesso topic ntfy globale — nessun modo per l'esterno di
scegliere canale/silenziarsi, e niente push personale per l'esterno (solo
l'admin aveva un topic ntfy configurato via `.env`).

Pagina `/portale/impostazioni` (self-service, ogni utente modifica solo le
proprie preferenze — `current_user.id` dalla sessione, mai un id passato dal
client):
- checkbox email on/off (default **on**, per non silenziare nessuno per
  errore alla creazione dell'account)
- checkbox push on/off (default **off** — richiede un topic personale)
- campo testo `ntfy_topic`, validato con whitelist `^[A-Za-z0-9_-]{1,64}$`
  (charset consigliato da ntfy.sh): un topic con spazi/caratteri strani non
  farebbe fallire la request in modo rumoroso (`send_push` la costruisce
  come `f"{server}/{topic}"` e il fallimento è inghiottito dal
  try/except "mai bloccare la request" già esistente), l'utente si
  ritroverebbe solo a non ricevere mai nulla senza capire perché.

`UtentiRepository.set_notifiche()` rifiuta `push_enabled=True` con topic
vuoto (stato incoerente che fallirebbe in silenzio). 3 nuove colonne su
`utenti` (`notify_email_enabled` INTEGER DEFAULT 1, `notify_push_enabled`
INTEGER DEFAULT 0, `ntfy_topic` TEXT DEFAULT ''), migrate con `ALTER TABLE`
avvolta in try/except (stesso pattern di `auto_cortesia_repository.py`).

`notify_admin_nuova_attivita` rinominata `notify_push_nuova_attivita`
(era già generica per topic, solo il nome suggeriva "solo admin"): ora
usata sia per il topic globale admin sia per il topic personale
dell'esterno. `_notifica_esterni_assegnati` (routes.py) legge
`u.notify_email_enabled`/`u.notify_push_enabled`/`u.ntfy_topic` per ogni
assegnatario invece di mandare sempre email.

## Stato pratica nel portale esterno

Motivazione: l'elenco `/portale` mostrava numero/cliente/veicolo/data
sinistro ma non lo stato della pratica — l'esterno non poteva capire a
colpo d'occhio quali pratiche erano ancora attive senza aprirle una per
una.

`routes_portale.py::portale_list()` ora legge lo stato corrente di ogni
pratica assegnata via `PraticaStatoRepository.get_stato(numero)` (stesso
pattern N+1 già usato lì per `wincar_repo.get_pratica` — liste tipicamente
piccole, 10-15 pratiche per utente esterno, nessun metodo bulk necessario
a questa scala). Nessun metodo bulk esiste oggi in `PraticaStatoRepository`;
se in futuro un'agenzia dovesse avere decine di pratiche assegnate, valuta
un `get_stati_bulk(numeri) -> dict[int, PraticaStato]` con una singola
query `WHERE pratica_numero IN (...)`.

Template `portale_list.html`: stesso pattern badge già in uso su
`/pratiche/<n>` (`class="badge badge-stato badge-{{ stato_corrente }}"`,
default `"aperta"` se nessuno stato mai impostato — replica esatta della
logica in `pratica_detail.html`). Pratiche con stato `chiusa` ricevono la
classe riga `row-chiusa` (`style.css`: `opacity: 0.55`, `0.8` in hover) per
distinguerle visivamente dalle pratiche ancora attive.

## Stato pratica modificabile dall'esterno + nuovo stato "periziata"

`routes_portale.py::portale_pratica_detail()` carica anche `pratica_stato`/
`pratica_stato_storia`/`stati_disponibili`; `portale_pratica_detail.html`
replica esattamente la card "Stato pratica" di `pratica_detail.html`
(stesso markup, stesso dropdown, stesso campo note). Nuova route
`POST /portale/pratiche/{numero}/stato` (IDOR-safe via `_verifica_accesso`,
stesso pattern di note/eventi), `changed_by=utente.nome or utente.email`
(non hardcoded `"operatore"` come la route admin equivalente in
`routes_impostazioni.py` — piccola incoerenza pre-esistente, non toccata).
Ogni cambio stato dall'esterno notifica l'admin via push (`_notifica_admin`,
stesso helper già usato per nota/evento).

`pratica_stato_repository.py`: nuovo `STATO_PERIZIATA = "periziata"`
inserito in `STATI` tra `perito_nominato` e `in_liquidazione` — la
posizione nella tupla conta, `auto_transition()` fa "upgrade" solo in
avanti basandosi sull'indice. Nessuna auto-transizione AI verso questo
stato (resta manuale, come già per aperta/chiusa). Nuova classe CSS
`badge-periziata`/`badge-teal` (teal, per non confondersi con l'arancio di
"perito nominato" o il viola di "in liquidazione").

## CSRF su tutti i form

Prima esteso solo al login (debito tecnico segnalato fin dalla fase 2).
`template_context_processor` in `web/auth.py` ora inietta `csrf_token` in
ogni pagina renderizzata (tutti i router tranne `routes_auth.py`, che
gestisce il proprio); `base.html` lo espone anche come
`<meta name="csrf-token">` nell'head, presente su ogni pagina. Tutti i 41
form `<form method="post">` del progetto portano un campo hidden
`csrf_token`.

`AuthMiddleware.dispatch()` verifica il token su ogni richiesta POST
(tranne `/login`, che ha la propria verifica dedicata con errore mostrato
sulla pagina invece del 403 generico).

**Bug Starlette scoperto e risolto durante l'implementazione** (non
teorico — rompeva silenziosamente OGNI form della app, non solo gli
upload): `BaseHTTPMiddleware` di Starlette usa `_CachedRequest`, che
replica il body verso l'app downstream SOLO se in `dispatch()` è stato
chiamato `Request.body()` (mette in cache `request._body`). Se invece si
chiama solo `Request.form()` — che usa `Request.stream()` internamente,
**anche per `application/x-www-form-urlencoded`**, non solo multipart —
lo stream risulta "consumato" ma NON cache-ato, e la route sottostante
riceve un body VUOTO: ogni `Form(...)` richiesto sparisce, 422 "Field
required". Fix in `_submitted_csrf_token()`: chiamare `await
request.body()` PRIMA di `await request.form()` — mette in cache i byte
grezzi, che `.form()` userà al posto dello stream live, e che la route
downstream riceverà intatti.

I form `multipart/form-data` (i 3 upload PDF: cessione firmata, verbale
uscita/rientro firmato) restano ESCLUSI dal controllo a livello di
middleware (`_is_multipart()` guarda il Content-Type) per non bufferizzare
in memoria l'intero file (fino a 20MB) ad ogni richiesta solo per leggere
un campo di testo. Verificano il CSRF da sole, con un parametro
`csrf_token: str = Form("")` e `verify_csrf()` esplicito a inizio route.

Test di regressione (non solo "il form passa col token giusto", anche "un
POST senza/con token falso viene bloccato"): `test_post_generico_senza_
csrf_token_rifiutato`/`..._con_csrf_token_falso_rifiutato` in
`test_auth.py` per il path generico, `test_upload_senza_csrf_token_
rifiutato`/`..._con_csrf_token_falso_rifiutato` in
`test_cessione_upload_route.py` per il path multipart.

## Reminder schedulati "il giorno prima"

Ultimo pezzo della roadmap v3.0 collaborazione. `scripts/send_event_
reminders.py` (+ `send_event_reminders.bat`), stesso pattern di
`run_polling.py`: lock file (`event_reminders.lock`, stale dopo 30 min),
logging su file con rotazione (`event_reminders.log`), mai un'eccezione non
gestita interrompe silenziosamente lo script (`try/except Exception` a
monte di `run_once()`, exit code 1). Pensato per Task Scheduler una volta
al giorno (guida in `docs/SETUP_PRODUCTION.md` §5.6) — **non** un lead
time configurabile per evento (scope tenuto volutamente semplice: fisso a
"il giorno prima", come da richiesta originale).

Pipeline: `PraticaEventiRepository.list_domani()` (nuovo metodo, `WHERE
date(data_evento) = date('now', '+1 day')`) → per ciascun evento non ancora
notificato (`reminder_gia_inviato()`/`segna_reminder_inviato()`, dedup su
nuova tabella `pratica_eventi_reminder`, `UNIQUE(evento_id)` — stesso
pattern di `pec_sla_reminder`) → push all'admin (topic globale) + per ogni
esterno assegnato alla pratica, email/push secondo le sue preferenze
self-service (`notify_email_enabled`/`notify_push_enabled`/`ntfy_topic`,
stessa logica già usata in `_notifica_esterni_assegnati` di `routes.py`,
duplicata qui perché lo script non deve dipendere dal layer web).

## Calendario mensile

`GET /calendario` (admin, tutte le pratiche) e `GET /portale/calendario`
(esterno, solo pratiche assegnate) — vista mensile stile Google Calendar,
template condiviso `calendario.html` (stessa cartella `web/templates/` per
entrambi i router) con `pratica_link_base` a distinguere i link
(`/pratiche` vs `/portale/pratiche`).

`PraticaEventiRepository.list_mese(anno, mese, pratica_numeri=None)`:
filtro `substr(data_evento, 1, 7) = 'YYYY-MM'` (più leggibile di un
BETWEEN con calcolo del primo/ultimo giorno del mese, e non richiede
gestire l'anno bisestile a mano). Griglia mese (`calendar.Calendar.
monthdatescalendar`, lunedì primo giorno) e navigazione prec/succ calcolate
in `_contesto_calendario()` (routes.py, riusata da routes_portale.py —
stesso pattern di condivisione già in uso per `_allegati_con_url`/
`_parse_date`/`resolve_pratica_file`).

## Modifica/eliminazione note (admin)

`PraticaNoteRepository.update()`/`.delete()` nuovi, stesso pattern IDOR-safe
di `PraticaEventiRepository.delete()` (`pratica_numero` obbligatorio nel
WHERE). Route `POST /pratiche/{numero}/note/{nota_id}/modifica` e
`/elimina`, solo admin (router `routes.py` è admin-only by default). Gli
utenti esterni continuano a poter solo aggiungere note, non modificarle né
eliminarle — scelta esplicita, non un'omissione.

## Home admin: ultime pratiche invece di suggerimenti statici

`home()` in `routes.py`: quando non c'è una ricerca in corso, invece del
box statico "Suggerimenti rapidi" chiama `repo.search_pratiche(limit=20)`
senza filtri — che ordina già per `F_NUMPRA DESC` (i numeri pratica
WinCar sono progressivi, quindi "ultime pratiche" in pratica), zero nuove
query. Stessa tabella `results-table` già usata per i risultati di ricerca.

## Widget "Prossimi appuntamenti" con cliente/targa

Il widget mostrava solo titolo evento + numero pratica. `_arricchisci_
eventi_con_pratica(eventi, repo)` (routes.py, condivisa con
routes_portale.py) aggiunge cliente/targa leggendo `repo.search_pratiche
(numero=..., limit=1)` per ogni evento — tollera errori PER SINGOLO evento
(un fallimento WinCar su una pratica non deve far sparire l'intero widget,
solo quella riga resta senza cliente/targa). Il context passato al
template cambia forma: da `list[Evento]` a `list[dict]` con chiavi
`evento`/`cliente`/`targa` — i template (`index.html`, `portale_list.html`)
fanno `{% set e = item.evento %}` all'inizio del loop.

## UI responsive tablet/telefono

Audit statico (nessun browser disponibile nell'ambiente di sviluppo per
screenshot reali — verificare comunque su un dispositivo vero dopo il
deploy). Tre gap strutturali trovati e corretti in `style.css`:

- **`.topnav`**: 11 voci lato admin non ci stanno su tablet/telefono. Prima
  non aveva alcuna gestione overflow — un flex item senza `min-width:0`
  dentro un flex container (`.topbar`) non si restringe mai sotto la sua
  larghezza di contenuto, quindi l'eccesso si propagava a tutta la pagina
  (scroll orizzontale dell'intero sito, bug mobile classico). Fix: `.brand`
  e `.user-box` (già) `flex-shrink:0`, `.topnav` `min-width:0;
  overflow-x:auto` — scorre da sola invece di rompere il layout, a
  qualunque larghezza (non gated da breakpoint, si applica sempre).
- **Tabelle senza wrapper scrollabile** (`compagnie_list.html`,
  `utenti_list.html`, `risposte_list.html`, ecc. — `.results-table` ha già
  una strategia dedicata, nasconde 2 colonne sotto 640px, ma restava
  comunque esposta allo stesso rischio): `@media (max-width: 900px) {
  .results-table, .table { display:block; overflow-x:auto; white-space:
  nowrap; } }` — tecnica standard (anonymous table box), nessun wrapper
  `<div>` da aggiungere nei template.
- **Form inline non andavano a capo**: nuova nota/evento, assegna
  collaboratore, ecc. usano `style="display:flex; gap:8px;
  align-items:flex-end;"` diretto in HTML (31 occorrenze in 12 template).
  Nessuno dichiara `flex-wrap` inline, quindi `.card form { flex-wrap:
  wrap; }` (+ `.card form > * { min-width: 0; }`) in un media query si
  applica senza bisogno di `!important` — dropdown/textarea/bottone vanno a
  capo invece di restringersi fino a rompersi. `.form-inline-stato` aveva
  già `flex-wrap:wrap` nella propria classe, non serviva.

Non toccati (già responsive): `.grid` (auto-fit grid nativo), `.foto-grid`
(auto-fill grid nativo), `.pratica-header`/`.pratica-actions` (già
flex-wrap), `.calendar-grid` (breakpoint dedicato già aggiunto in fase 5H),
`.dropzone` (nessuna larghezza fissa).

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
`delete_mail()` e `hard_delete_mail()` scrivono `UPDATE mail_in SET ignorata=1`
(mai DELETE fisico). `max_uid()` include `ignorata=1` → UID non scende → fetcher
non riscarica mai una mail già vista, neanche se eliminata.

`hard_delete_mail()` cancella in più `mail_classificate` (rimozione completa dalla UI),
ma la riga `mail_in` rimane come tombstone per bloccare il re-download via UNIQUE
su `(casella, uid_imap)`.

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
- **Prod**: macchina fisica separata in carrozzeria, hostname **LYSAUTO**
  (NON il laptop di sviluppo) — `C:\LYSApp\lys-workflow-hub` (Windows), Task
  Scheduler, task "LYS Workflow Hub" account `lysau`. Raggiungibile da qui
  via share di rete `\\LYSAUTO\WinCar` (mappata `Z:`) o AnyDesk/RDP. Query
  `schtasks`/processi vanno puntate esplicitamente a LYSAUTO — MAI assumere
  che questo laptop sia prod solo perché il path locale è identico
  (`C:\LYSApp\...` esiste anche qui, ma è solo l'ambiente di test).
- **gh CLI**: `"/mnt/c/Program Files/GitHub CLI/gh.exe"` (non nel PATH WSL)
- **Python env**: `.venv` nella root del repo
- **DB**: `data/lys_hub.db` (gitignored)
- **Chiave API**: in `.env` (gitignored)

---

## Stato attuale

Versione **4.21.3** in produzione su `main` / `https://hub.lysauto.it`.
Sviluppo contabilità gestionale + SDI (**4.22.0**) sul branch
`feature/contabilita-sdi`, non ancora in produzione. Changelog per-commit in
`git log`; le decisioni tecniche non ovvie dal codice (formati, gotcha, cause
di bug reali) restano documentate nelle sezioni sopra, per sottosistema.

**4.25.0 — Contabilità gestionale, Fase 4 (branch `feature/contabilita-sdi`)**:
coda smistamento fatture passive (assegnazione categoria/pratica + split,
`/contabilita/fatture/passive/da-collegare`) + dashboard costi/ricavi per
categoria/periodo (`/contabilita/report`). Ciclo contabilità completo.

**4.24.0 — Contabilità gestionale, Fase 3 (branch `feature/contabilita-sdi`)**:
integrazione SDI — `integrations/sdi.py` (client astratto, Fake + Openapi),
`workflows/contabilita/sdi_import.py` (parser XML FatturaPA + import attive da
WinCar / invio SDI / sync passive), `scripts/run_sdi_poll.py`, UI
`/contabilita/fatture`. Vedi sezione dedicata sopra.

**4.23.0 — Contabilità gestionale, Fase 2 (branch `feature/contabilita-sdi`)**:
scheda economica pratica (entrate/uscite/margine + ripartizione per categoria
+ fatture collegate) come sezione admin-only in `pratica_detail.html`, mai nel
portale esterno. Vedi sezione dedicata sopra.

**4.22.0 — Contabilità gestionale, Fase 1 (branch `feature/contabilita-sdi`)**:
modello dati + CRUD di categorie e movimenti (entrate/uscite analitiche per
pratica e categoria). Nessuna contabilità fiscale. Vedi sezione dedicata sopra.

**4.21.3 — Fix: token FCM condiviso tra due account sullo stesso device**:
segnalato dall'utente admin — riceveva push anche delle proprie note,
instradate come se fossero dirette a un collaboratore esterno. Causa:
`UtentiRepository.set_fcm_token()`/`set_fcm_token_web()` scrivevano il
token SOLO sulla riga dell'utente che si stava registrando, senza mai
toglierlo a un altro utente che lo avesse già — se lo stesso telefono
viene usato prima per un account esterno di test poi per il login admin
(`PushNotifications.register()` in `base.html` gira ad ogni apertura
pagina, stesso token fisico), i due account restavano registrati con lo
stesso token per sempre: ciascuno riceveva anche le push dell'altro. Fix:
prima di scrivere il nuovo token sulla riga dell'utente corrente, lo si
toglie (`UPDATE ... SET fcm_token = ''`) da qualunque ALTRO utente che lo
avesse — un token fisico appartiene a un solo utente alla volta, non solo
il contrario. Autorisolutivo lato utente: basta riaprire l'app da loggato
(anche solo una navigazione) perché la registrazione seguente ripulisca lo
stato duplicato, nessun intervento manuale sul DB necessario. Test di
regressione aggiunti in `test_notifiche_preferenze.py`.

**Chiusura suggerita dal code-review**: `/logout` (`web/routes_auth.py`)
prima puliva solo la sessione, mai il token FCM — un device disconnesso
restava comunque registrato e continuava a ricevere push su pratiche non
più visibili, finché non arrivava la prossima registrazione (self-healing
ma non deterministico). Ora `/logout` azzera esplicitamente sia
`fcm_token` sia `fcm_token_web` dell'utente uscito, best-effort (un errore
qui non deve mai impedire il logout). Test in `test_auth.py`.

**4.21.2 — Fix: cambio stato admin non notificava l'esterno**: segnalato
dall'utente ("ho aggiornato lo stato senza scrivere nulla e non è arrivata
nessuna notifica"). Causa: `POST /pratiche/{numero}/stato` non vive in
`web/routes.py` insieme a nota/upload/cessione (dove sta tutta la logica di
notifica/reminder), ma in `web/routes_impostazioni.py` — una route scritta
prima che quel sistema esistesse e mai riallineata dopo. Non chiamava
`_notifica_esterni_assegnati`, non creava il reminder esterno, non
risolveva il reminder admin pendente; usava perfino `changed_by="operatore"`
hardcoded invece del nome dell'admin autenticato (nessun `Depends
(require_admin)` esplicito, solo il gate a livello di router — l'identità
non era mai stata necessaria finché non serviva comporre un messaggio di
notifica). Fix: aggiunto `admin: Utente = Depends(require_admin)`,
importata `_notifica_esterni_assegnati` da `routes.py` (nessun import
circolare, stesso pattern già usato da `routes_portale.py`), aggiunte le
stesse chiamate di risoluzione/notifica delle altre azioni admin. Zero test
coprivano questa route prima d'ora — aggiunta copertura in
`test_notifiche_collaborazione.py`. Nel farlo, scoperto (e sistemato) un
gap distinto nel fixture di test `admin_client`: `routes_impostazioni.py`
ha una propria `get_settings_dep` separata da `get_app_settings` (stesso
pattern di `get_portale_settings`) mai stata overridata nei test — un primo
giro di test è finito per sbaglio a scrivere stato reale sul
`data/lys_hub.db` di sviluppo invece che su un DB temporaneo, ripulito a
mano dopo essersene accorti.

**4.21.1 — Fix titolo reminder esterno**: segnalato dall'utente via
screenshot — il widget "Notifiche in attesa" lato esterno mostrava titoli
in stile oggetto email ("[LYS Hub] Nuova nota sulla pratica 840") invece
che brevi come lato admin ("Nuova nota · Pratica 840"). Causa:
`_notifica_esterni_assegnati` (`web/routes.py`) riusava lo stesso `subject`
sia per l'oggetto email sia per ntfy/FCM/titolo reminder — a differenza di
`_notifica_admin`, che tiene sempre distinti `push_titolo` (breve) e
`messaggio`. Fix: `costruisci_messaggio` ora restituisce una tupla a 3
(`push_titolo, subject, body_text`) in tutti e 4 i call site (nota, evento,
upload, cessione firmata) — `subject` esteso resta solo per l'email,
`push_titolo` breve va a ntfy/FCM/reminder. Cambia anche il titolo delle
push reali su ntfy/FCM lato esterno, non solo il widget. Test di
regressione aggiunto.

**4.21.0 — Reminder ricorrente per notifiche esterni non gestite**:
simmetrico al reminder admin del 4.16.0, lato collaboratore esterno. Se
l'admin agisce su una pratica (nota/evento/upload/cessione firmata) e nessun
collaboratore assegnato agisce a sua volta (nota/evento/stato/upload) entro
24h né lo silenzia manualmente, il reminder viene rimandato ad ogni ciclo di
`run_polling.py` finché non viene risolto. Nuovo
`core/esterno_pratica_reminder_repository.py`
(`EsternoPraticaReminderRepository`, tabella separata
`esterno_pratica_reminder` nello stesso DB — indici col nome distinto da
quelli admin, altrimenti `CREATE INDEX IF NOT EXISTS` su un nome già
occupato salterebbe silenziosamente la creazione e romperebbe l'unicità del
reminder attivo, coperto da test dedicato). File duplicato di proposito
invece di parametrizzare una classe condivisa per il nome tabella — stesso
stile già scelto per `fcm_token`/`fcm_token_web`.

Creato/aggiornato da `_notifica_esterni_assegnati` (`web/routes.py`, stesso
punto dove notifica gli esterni), risolto da ognuna delle azioni scrittura
del portale esterno (`_upload_pratica`, `portale_aggiungi_nota`,
`portale_aggiungi_evento`, `portale_cambia_stato` in
`web/routes_portale.py`) o manualmente dal bottone "Segna come vista" nel
nuovo widget "Notifiche in attesa" in home portale (`portale_list.html`,
stesso markup del widget admin), POST a nuova route
`/portale/pratiche/{numero}/reminder/silenzia` — nascosto e non
raggiungibile dal ruolo "supervisore" (sola lettura, non può mai risolverlo
agendo). Resend in `run_polling.py` (nuovo blocco 7, dopo quello admin)
rilegge gli assegnati CORRENTI della pratica ad ogni resend (non quelli al
momento della creazione) via `PraticaAssegnazioniRepository`, per seguire
eventuali riassegnazioni; se una pratica finisce senza alcun assegnato il
reminder si auto-risolve invece di rimanere "attivo" all'infinito senza che
nessuno possa vederlo o silenziarlo.

**4.20.0 — Export CSV pratiche (admin + portale esterno, solo browser)**:
nuova voce di menu "Esporta" — nascosta in app (`html.is-app
.csv-export-link/.csv-export-page { display:none }`), perché il download
da form POST non funziona nella WebView Capacitor senza l'intercettazione
`.js-app-download`, deliberatamente non implementata qui. Pagina dedicata
(`pratiche_esporta.html`/`portale_esporta.html`) con: checkbox per riga +
"seleziona tutto" (solo righe visibili secondo i filtri), filtro testo
(numero/cliente/targa), filtro **stato** a checkbox multi-selezione
(entrambi i ruoli — nessuna selezione = tutti gli stati) e filtro
**collaboratore** a checkbox multi-selezione (**solo admin**, lista da
`UtentiRepository.list_esterni()`, verifica assegnazione via nuovo
`PraticaAssegnazioniRepository.mappa_utenti_per_pratica()` — dict bulk
pratica→lista utente_id, per evitare una query per pratica su
potenzialmente migliaia di righe). Filtri sempre AND tra loro, applicati
sia lato client (JS aggiorna la tabella dal vivo) sia lato server nel
`POST .../esporta.csv`: se non si seleziona nessuna riga si esportano
tutte quelle che passano i filtri correnti, altrimenti solo le righe
selezionate (a loro volta ulteriormente filtrabili).

Colonne del CSV identiche a quelle della home esterno (Numero, Cliente,
Targa, Veicolo, Data sinistro, Stato) — mai le altre colonne solo-admin
(Collaboratore). Separatore `;` (non `,`) + BOM UTF-8: Excel in locale
italiano tratta la virgola come separatore decimale e mostrerebbe
caratteri accentati corrotti senza BOM. Nuovo
`PraticaStatoRepository.stati_correnti()` (stesso pattern a subquery
`ROW_NUMBER()` di `count_by_stato()`) per il filtro stato bulk. Lo
scoping di sicurezza del portale esterno è identico a `/portale` — un
utente non può esportare una pratica non sua nemmeno passando il numero
a mano nella selezione (coperto da test).

`WinCarRepository` non espone una variante "tutte le pratiche" davvero
illimitata (solo `search_pratiche(limit=N)`, `SELECT TOP N` SQL): la
pagina di export usa `limit=20000`, pragmatico ma non un vero unbounded —
copre l'intero storico realistico di una carrozzeria senza dover toccare
l'accesso read-only a WinCar per aggiungere un metodo dedicato.

**4.19.0 — Giro di fix UX/UI mobile (audit + segnalazioni reali)**: audit
statico richiesto sui template/CSS (nessuna modifica), poi fix in due
ondate — prima quelli trovati dall'audit, poi 5 bug reali segnalati da un
utente esterno via screenshot da browser telefono, con causa comune non
notata subito: gran parte del redesign mobile-first di `style.css` era
gated solo su `html.is-app` (JS, `window.Capacitor` esiste solo nel
wrapper Android), quindi il browser mobile normale — canale reale per
collaboratori esterni e operatori, non solo l'app — restava sullo skin
desktop non responsive. Fix principale del giro: **stessa duplicazione
`html:not(.is-app)` gated su `@media` invece che su `html.is-app`**,
applicata punto per punto via segnalazioni reali, non un redesign
preventivo completo:
- **Bug**: "Scatta foto" sovrascriveva lo scatto precedente invece di
  accumularlo (`DataTransfer` nuovo ad ogni tap) — `pratica_detail.html`,
  `portale_pratica_detail.html`, `operatore_ingresso_detail.html`.
- **Bug**: nessun feedback di caricamento su generazione documenti/upload
  fuori dall'app (rischio doppio submit su rete lenta, architettura a
  full-page-reload) — nuovo listener generico su `submit` in `base.html`,
  disabilita i bottoni + "Attendere…" con rete di sicurezza 8s, non gated
  su `isNativePlatform()`.
- **Bug**: `login.html` non estende `base.html`, quindi non riceveva mai
  la classe `html.is-app` — unica schermata dell'app rimasta sullo skin
  browser (visibile ad ogni logout, anche dal gate biometrico).
- Tabelle→card e touch target 44px estesi al browser mobile (prima solo
  app), scorciatoie rapide (pillole) in cima alle pagine pratica dense,
  bottone "Torna alla ricerca" in fondo pagina reso visibile ovunque,
  contrasto `--lys-grey-500` alzato da ~4.54:1 (al limite AA) a ~5.6:1.
- **Bug**: colonna "Veicolo" (e "Data sinistro") sparita nelle card di
  `/home` sotto 640px — una vecchia regola `nth-child(4)/(5)` genererica
  di `.results-table` (precedente al layout a card dedicato di
  `.pratiche-table`) vinceva per specificità e nascondeva quelle colonne
  per posizione, ovunque, non solo sopra i 599px dove servirebbe da
  fallback. Escluso `.pratiche-table` da quella regola.
- **Bug** (5 segnalati insieme via screenshot da un utente esterno,
  Valentino, da browser Samsung Internet): (1) menu accavallato/tagliato
  (hamburger esteso al browser mobile, stesso meccanismo checkbox+label
  già usato in app); (2) riquadro "Nuova nota" minuscolo (`rows="2"` →
  `"4"`); (3) impossibile chiudere una foto aperta (bottone chiudi da
  `position:absolute` a `fixed` — poteva non essere raggiungibile a
  seconda dello scroll/stacking del genitore — più tap-sulla-foto-per-
  chiudere da browser, dove non c'è pinch-zoom con cui confliggere); (4)
  nessun tasto "Scatta foto" da browser (il plugin `@capacitor/camera`
  esiste solo in app — aggiunto un secondo input nascosto con
  `capture="environment"`, apre la fotocamera diretta sui browser
  mobili, stesso meccanismo di accumulo via `DataTransfer` del fix
  sopra); (5) dopo il fix (2), segnalato che il riquadro note era
  "ancora piccolo" — in realtà un campo diverso, l'input nudo "Note
  (opzionale)" nel form di cambio stato pratica (non dentro
  `.form-field`, quindi fuori dal fix touch-target generico): stack a
  colonna di `.form-inline-stato` esteso anche al browser mobile.

Nessuna modifica nativa Android in questo giro — tutto server-side
(template/CSS/JS), live senza rebuild APK.

**4.18.0 — Riscrittura sblocco biometrico LYSApp + fix apertura documenti
admin in app** (APK `versionCode 9` / `versionName 4.10.0`, numerazione
separata dal repo): il gate biometrico introdotto in 4.12.0 girava su
`sessionStorage` (azzerato solo al backgrounding via `appStateChange`), un
segnale poco affidabile per un vero cold start del processo Android.
Riscritto per usare un plugin nativo dedicato, `ColdStartPlugin.java`
(`mobile/android/.../ColdStartPlugin.java`): uno `static volatile boolean
coldStart` ricreato solo alla vera morte/riavvio del processo Java — una
navigazione interna (link, reload nella stessa WebView) non lo tocca mai,
solo un riavvio reale del processo Android lo resetta a `true`. `volatile`
necessario perché `@PluginMethod` può girare su thread diverso da quello
della chiamata JS. Auto-setup della preferenza al primo login (nessuna
attivazione manuale in Impostazioni, l'utente non deve "scoprire" la
funzione), nessun bypass. Reload pagina forzato su cold start reale (non
più solo su richiesta biometria).

**Hardening di sicurezza da code-review dopo il merge**: (1) le chiamate
sincrone ai plugin Capacitor (`NativeBiometric.verifyIdentity`,
`ColdStart.consume`, `NB.isAvailable`) possono lanciare eccezioni
*sincrone* su un mismatch di versione bundle/APK, bypassando `.catch()` —
avvolte in `try/catch` con fallback esplicito ("nessuna verifica riuscita
→ resta bloccato o bottone si riabilita", mai un'eccezione non gestita che
lascia l'overlay/bottone in uno stato indeterminato); rete di sicurezza
anche in `_biometria_toggle.html` per il bottone "Disattiva". (2) bypass
teorico del lock screen via back-forward cache (bfcache) di Android: una
pagina ripristinata dalla bfcache non riesegue gli script, quindi il gate
non si ripresentava; fix con un listener `pageshow` che, quando
`event.persisted` è vero, nasconde subito il documento e rilancia
`controllaLock()`.

**Fix bug reale: admin non riusciva ad aprire documenti/allegati in
app**: root cause in `base.html` — un unico blocco Jinja `{% if
current_user and not current_user.is_admin and fcm_web_configured %}
...{% endif %}` avvolgeva DUE script indipendenti (il Web Push FCM per
browser, giustamente non-admin-only, e lo script di apertura file
`fetch`+`Filesystem`+`Share` per l'app, che invece serve a TUTTI gli
utenti loggati incluso l'admin) — la condizione `not is_admin` nascondeva
l'intero script di apertura file anche per l'admin. Diviso in due blocchi
`{% if %}` indipendenti, ciascuno con la propria condizione. Bug
preesistente dal commit che aveva introdotto quello script (non
introdotto in questo giro).

Branch `feat/biometric-lock-rewrite` mergiato in `main`, poi cancellato
(locale+remoto) insieme alla release/tag di test
`android-biometric-lock-rewrite-test`. La release ufficiale
`android-lysapp-v1` — fino a questo giro ferma a `versionCode 8`/`4.9.1`,
un build PRECEDENTE all'intera feature di sblocco biometrico (verificato:
`ColdStartPlugin.java` non esisteva ancora a quel commit) — è stata
rigenerata e aggiornata con l'APK corrente (`4.10.0`) + `latest.json`
allineato, così il check-aggiornamento in-app e il sideload puntano
entrambi alla build giusta.

**4.17.0 — Eliminazione file caricati (esterno, solo i propri)**: un
collaboratore esterno può ora eliminare foto/documenti che ha caricato
lui stesso — mai quelli caricati dall'admin o da un altro collaboratore.
L'admin continua a poter eliminare qualunque foto senza restrizioni
(comportamento invariato), ma non aveva mai avuto una route per eliminare
documenti — introdotta anche quella, riusata solo dal lato esterno per
ora.

Nuovo `core/pratica_file_uploader_repository.py`
(`PraticaFileUploaderRepository`): traccia chi ha caricato ciascun file
(chiave = path assoluto, sempre univoco perché `save_upload()` non
sovrascrive mai). `caricato_da(path) is None` per un file caricato prima
di questa feature — fail-safe deliberato, "nessun proprietario noto" non
è mai un permesso implicito. `_salva_file_pratica()` (condivisa tra
upload admin ed esterno, `web/routes.py`) registra l'autore dopo ogni
salvataggio riuscito, best-effort (un fallimento della tracciatura non
blocca mai il salvataggio del file, lo rende solo non eliminabile dal suo
autore finché non lo elimina un admin).

La logica fisica di eliminazione foto (unlink + `.thumb` + pulizia
`Thumbs.thumb` + azzeramento `CARVEI.F_FOTO` se era l'ultima) è stata
estratta in `_elimina_foto_fisica()`, condivisa tra la route admin
(nessun controllo di proprietario) e la nuova route esterno (che verifica
la proprietà PRIMA di chiamarla). Nuova `_elimina_documento_fisico()`
per i documenti (più semplice, nessun side-effect WinCar).

Nuove route: `POST /portale/pratiche/{numero}/foto/elimina` e
`/documenti/elimina` — stessa catena di controlli delle altre route di
scrittura del portale (`_verifica_accesso` → `_richiedi_permesso_scrittura`
→ nuovo `_richiedi_proprietario_file`, 403 se il file non è tracciato
come caricato da quell'utente PROPRIO sotto quel numero pratica).
`PraticaFileUploaderRepository.eliminabile_da(numero, path, utente_id)`
verifica esplicitamente `pratica_numero` oltre a `caricato_da` — non basta
essere l'autore del file, l'invariante non dipende dal fatto che il
chiamante rivalidi comunque il path con `scan_allegati(numero)` subito
dopo (`_elimina_foto_fisica`/`_elimina_documento_fisico`, che condividono
la validazione path-vs-pratica tramite `_valida_path_pratica_o_403()`).
Copertura test dedicata all'IDOR cross-pratica (stesso utente, due
pratiche assegnate, file dell'una eliminato passando per l'URL
dell'altra) sia a livello repository sia HTTP end-to-end.

**4.16.1**: badge stato ("IN TRATTATIVA" ecc.) andavano a capo su due
righe nella lista pratiche admin quando la colonna era stretta —
`.badge` non aveva `white-space: nowrap`. Aggiunto globalmente (tutti i
badge, non solo stato): con table layout auto il browser allarga da solo
la colonna quando il contenuto non può più andare a capo, niente
larghezze fisse da gestire a mano.

**4.16.0 — Reminder ricorrente per notifiche admin non gestite**: se un
collaboratore agisce su una pratica (nota/upload/stato) l'admin riceve una
notifica (`_notifica_admin`, `web/routes_portale.py`), ma prima non c'era
modo di ricordarsela — l'utente faceva uno screenshot della tendina
notifiche per non perdere pratiche multiple aggiornate insieme. Nuovo
repository `core/admin_pratica_reminder_repository.py`
(`AdminPraticaReminderRepository`, tabella `admin_pratica_reminder`, un
reminder "attivo" per pratica alla volta): `_notifica_admin` ne crea/
aggiorna uno ad ogni notifica (`upsert_attivo`, aggiorna solo il testo se
già attivo, NON resetta il timer — altrimenti un collaboratore che tocca
la pratica ogni ora bloccherebbe per sempre il resend). Si risolve in due
modi:
- **Automaticamente**: quando l'admin agisce sulla stessa pratica — nota
  (`pratica_aggiungi_nota`) o upload foto/documenti
  (`_upload_pratica_admin`), entrambe in `routes.py`. L'admin non ha
  un'azione di cambio-stato propria (`/pratiche/{n}/stato` esiste solo
  lato `/portale`, per i collaboratori) quindi non è tra i trigger.
- **Manualmente**: bottone "Segna come vista" nel nuovo widget "Notifiche
  in attesa" in home admin (`index.html`, sopra "Prossimi appuntamenti"),
  POST a `/pratiche/{numero}/reminder/silenzia`.

Resend ogni 24h agganciato a `scripts/run_polling.py` (già schedulato 2
volte/giorno in Task Scheduler — decisione esplicita con l'utente: niente
nuovo script/Task Scheduler dedicato) — nuovo blocco a fine
`run_once()`, stesso punto/stile del blocco SLA escalation già presente:
`list_scaduti(soglia_ore=24)`, resend ntfy + FCM a tutti gli admin con
token registrato, `segna_rimandato()`. Cadenza reale legata al polling
(non 24h esatte), stesso compromesso già accettato per SLA escalation e
`send_event_reminders.py`. Nessun test end-to-end su `run_polling.py`
(zero test pre-esistenti su questo script, troppe dipendenze esterne
IMAP/AI/WinCar da mockare) — coperto solo a livello di repository
(`list_scaduti`/`segna_rimandato`/`upsert_attivo`/`risolvi_per_pratica`).

**Fix da code-review prima del rilascio**: `upsert_attivo()` era in origine
un SELECT-then-INSERT/UPDATE separato (unico repository in `core/` a farlo
invece di un indice `UNIQUE` — convenzione reale del progetto, vedi
`sollecito_repository.py`/`pratica_assegnazioni_repository.py`/ecc.): due
azioni quasi-simultanee sulla stessa pratica potevano creare due reminder
"attivi" per lo stesso numero. Fix: indice parziale
`uq_admin_pratica_reminder_attivo` (`WHERE stato='attivo'`) + UPSERT
atomico (`INSERT ... ON CONFLICT(pratica_numero) WHERE stato='attivo' DO
UPDATE`), con test di regressione che verifica l'`IntegrityError` su un
secondo INSERT diretto. Aggiunto anche l'hook di auto-risoluzione mancante
su `pratica_aggiungi_evento` (admin) — c'era solo su nota e upload.

**4.15.0 — Giro 2 su admin-in-app + FCM all'admin**: test reale su device
dopo il 4.14.0 ha trovato altri gap, tutti risolti nello stesso giro:
- **Bug architetturale trovato nel 4.14.0**: `Browser.open()` (Chrome
  Custom Tabs) usato per `target="_blank"` gira in un processo Chrome
  separato con cookie jar proprio, isolato da quello della WebView
  dell'app — la sessione di login non passa, quindi ogni link autenticato
  (documenti, .eml, allegati) apriva la pagina di login invece del file
  ("non vedo l'anteprima"). Fix in `base.html`: stesso link ora fa
  `fetch()` dentro la WebView (cookie della pagina, niente problemi di
  auth) → `Filesystem` (cache) → `Share` — stesso meccanismo già
  necessario per i download da form POST, unificato in un solo helper
  `lysFetchAndOpen`. `Browser.open()` resta solo per link di altra origin
  (es. GitHub, non serve la sessione dell'app).
- **"dal" nella colonna Veicolo**: non era sui dati della pratica singola,
  era nella lista pratiche — `portale_list.html` già tronca
  `.split(" dal ")[0]` sul modello, `index.html` (admin) no. Applicato
  identico, anche colonna **Stato** con badge aggiunta alla lista admin
  per parità di layout con `/portale` (sia in app che da browser).
- **Zoom pinch + scatta foto**: portati in `pratica_detail.html` (admin),
  stesso codice di `portale_pratica_detail.html`, gating
  `isNativePlatform()` invariato (solo in app, la selezione multipla con
  pressione prolungata di `/portale` non è stata riproposta, non
  richiesta).
- **"Torna alla ricerca"**: ora pulsante (`btn btn-sm`) come l'esterno.
- **`/foto` non scorrimento orizzontale**: la tabella usava una classe
  (`data-table`) mai stilata, fuori dalla strategia responsive generica
  (`.table`/`.results-table`, `overflow-x:auto` sotto i 900px) di tutte le
  altre tabelle admin. Cambiata classe, nessun'altra modifica.
- **Notifiche FCM all'admin loggato in LYSApp**: `_notifica_admin`
  (`routes_portale.py`, attività di un esterno su nota/evento/stato/
  upload) mandava solo ntfy (topic globale, richiede setup separato) — mai
  una push FCM nativa al token dell'app, anche se l'admin registra il
  token esattamente come un esterno (stesso script in `base.html`). Ora
  manda FCM a ogni admin attivo con token registrato
  (`_notifica_fcm_tutti_i_canali`, già esistente per gli esterni, esteso
  con un parametro `click_path` opzionale: il default `/portale/...`
  darebbe 403 a un admin, serve `/pratiche/{numero}`).

Nessuna modifica nativa Android in questo giro (solo template/CSS/JS
server-side + route Python) — **nessun rebuild APK necessario**, tutto
live via wrapper.

**4.14.0 — Admin funzionante dentro l'app Android**: finora nessuno aveva
mai testato il login admin dentro LYSApp — le pagine admin non erano state
pensate per la WebView Capacitor, solo `/portale`/`/operatore` lo erano.
Login admin nell'app ha esposto due gotcha reali della WebView (nessun
supporto multi-finestra, nessun `DownloadListener`), entrambi già noti e
risolti SOLO per il portale esterno (`Browser.open()` su
`.documento-link`) ma mai generalizzati:
1. **`target="_blank"` morto in WebView**: qualunque link con `target=
   "_blank"` (documenti pratica, allegati bozza/vandalismo/risposte, .eml
   PEC, ingresso officina, link "API") non apriva nulla al tap. Fix in
   `base.html`: un solo listener globale a livello di `document` (delega,
   non serve toccare ogni template) che intercetta il click su qualunque
   `a[target="_blank"]` e lo apre con `Browser.open()` (Chrome Custom
   Tabs). Sostituisce/rimuove l'handler locale `.documento-link` di
   `portale_pratica_detail.html`, ora ridondante. Aggiunto `target=
   "_blank"` anche a due link che non lo avevano (`.eml` PEC in
   `pec_inviata_detail.html` e `risposta_detail.html`).
2. **Download da form POST morto in WebView** (bug più serio: erano le
   funzioni principali admin — genera cessione, verbali, bozza
   vandalismo): i bottoni "Scarica .docx"/"Genera PDF"/"Scarica bozza
   .txt" fanno POST e la risposta ha `Content-Disposition: attachment`.
   Senza `DownloadListener` nella WebView la navigazione normale del form
   finisce su una pagina bianca e il file sparisce. Fix: bottoni marcati
   con classe `.js-app-download` (non l'intero form — alcuni form hanno
   anche bottoni che portano a pagine normali, es. "Procedi all'invio" in
   vandalismo) vengono intercettati in `base.html`, la POST viene rifatta
   via `fetch()` mantenendo l'encoding `application/x-www-form-urlencoded`
   originale (fondamentale: `FormData` nativo forzerebbe `multipart/
   form-data`, che farebbe saltare il controllo CSRF automatico di
   `AuthMiddleware` — quello scatta solo sui POST non-multipart), il blob
   risultante viene scritto in cache con `@capacitor/filesystem` e
   condiviso con `@capacitor/share` (share sheet Android: l'utente salva o
   apre con Word/Adobe/Drive). Entrambi i plugin erano già installati e
   già compilati nell'APK `versionCode 8` per la feature biometrica/
   update-check — **zero rebuild APK richiesto**, tutto JS lato server
   (wrapper live). Il download zip foto (`/pratiche/{n}/foto/zip`) resta
   deliberatamente nascosto in app (già così da prima, `html.is-app
   .foto-download-bar { display: none }`): stesso problema di
   `DownloadListener`, ma un bulk-zip su mobile ha poco senso, non
   riproposto col pattern share-sheet.

Nessuna modifica al CSS di layout: il redesign mobile-first sotto
`html.is-app` (già esteso, non solo `/portale`) e l'`overflow-x: auto`
generico su `.table`/`.results-table` coprivano già le pagine admin senza
rotture — il problema era solo funzionale (download), non grafico.

**4.13.0 — Ingressi officina (nuovo ruolo "operatore")**: quando entra un
veicolo nuovo, un operatore d'officina crea dall'app un "ingresso" —
cliente, targa, note + 4 documenti (CID, documento identità, libretto,
foto danno) scattati/caricati — che vive SOLO nel nostro SQLite
(`ingressi_officina`/`ingressi_officina_file`,
`core/ingressi_officina_repository.py`), mai in WinCar. Decisione esplicita
presa con l'utente: niente scrittura diretta della pratica in WinCar (il
`.mdb` resta read-only per invariante di progetto, unica deroga isolata in
`wincar_carvei_write.py` per un solo campo — creare pratiche intere
richiederebbe conoscere lo schema completo di `CARVEI`, mai investigato).
L'admin vede la coda in `/ingressi`, crea la pratica vera in WinCar A MANO
(fuori dall'app, come oggi), poi "collega" l'ingresso inserendo il numero
pratica: ogni file di staging (`IngressiOfficina/<id>/...`, cartella
sorella di `Pratiche/`, mai scansionata da WinCar) viene riletto e salvato
con `save_upload()` già esistente — thumb WinCar e flag `CARVEI.F_FOTO`
per le foto arrivano gratis, stesso codice usato per gli upload normali.
Nuove route `web/routes_operatore.py` (operatore, non admin-only, guard
locale `_richiedi_operatore`) e `web/routes_ingressi.py` (admin-only).
**Bug reale trovato da code-review prima del rilascio**: il loop di copia
file in `ingresso_collega` girava PRIMA della transizione di stato
atomica — un retry dopo un fallimento parziale o un doppio submit
ravvicinato ricopiava gli stessi file con nome nuovo (`save_upload` non
sovrascrive mai), duplicandoli silenziosamente nella cartella pubblica
della pratica WinCar. Fix: `repo.collega()` (atomico, `WHERE
stato='in_attesa'`) avviene PRIMA di toccare qualunque file — un secondo
tentativo fallisce subito (400 se sequenziale, 409 se davvero concorrente)
senza mai duplicare nulla, con test di regressione dedicato. Nessuna
modifica alla APK Android: l'app è un wrapper live, `/operatore` è
servita dallo stesso backend come `/portale`, nessun plugin nativo nuovo.

**4.12.0 — LYSApp: canale notifiche, check aggiornamento, sblocco biometrico**
(APK `versionCode 8` / `versionName 4.9.1`, numerazione separata dal repo):
- **Canale FCM ad alta importanza**: `PushNotifications.createChannel()`
  (importance `HIGH`, visibility `PRIVATE` — titolo/corpo possono contenere
  nome cliente/targa, non vanno in chiaro su lockscreen) in `base.html`,
  `channel_id: "lys_hub_activity"` nel payload di `send_fcm_push()`
  (`notifier.py`) — senza questo Android usava il canale di default della lib
  FCM (importance `DEFAULT`, niente heads-up/popup, solo tendina). Nessuna
  rebuild APK richiesta per questa parte (JS lato server, live).
- **Check aggiornamento in-app**: l'APK non è su Play Store (solo GitHub
  Release, sideload), nessun auto-update. `base.html` confronta
  `App.getInfo().build` (plugin `@capacitor/app`, nuovo) con un manifest
  `latest.json` pubblicato come secondo asset sulla stessa release
  `android-lysapp-v1` ad ogni build, scaricato dal CDN pubblico di GitHub
  (mai `api.github.com`, rate-limit 60/h non autenticato). Banner dismissibile
  con link download diretto dell'APK, throttle 1 check/giorno in `localStorage`
  (timestamp aggiornato solo su fetch riuscita, non su errore/offline).
- **Sblocco biometrico opt-in**: plugin `@capgo/capacitor-native-biometric`
  (fork mantenuto, l'originale `capacitor-native-biometric` dichiara "solo
  Capacitor 3/4" — scartato). Toggle in `/portale/impostazioni` (sezione
  `app-only`), preferenza in `localStorage` (device, non account), default
  OFF, attivata solo dopo una `verifyIdentity()` di conferma riuscita per
  evitare lock-out (`useFallback: true`, passcode device come alternativa).
  Overlay di blocco in `base.html` su cold start e su ripresa da background
  (`App.addListener('appStateChange', ...)`), con pulsante "Esci" per non
  restare bloccati se il sensore smette di funzionare. Non sostituisce il
  login/sessione cookie, blocca solo l'accesso fisico all'app già autenticata.
  **Due problemi reali trovati da code-review prima del rilascio**: (1) l'app
  è una MPA senza router client-side — ogni link ricarica l'intera pagina,
  quindi senza guardia il prompt biometrico sarebbe spuntato ad ogni tap
  invece che solo a cold start/resume; fix con flag `sessionStorage`
  (`lys_unlocked_session`, sopravvive alla navigazione full-page, azzerato
  solo su vero backgrounding). (2) lo sblocco da solo non copriva
  l'anteprima che Android cattura per il task-switcher (recent apps) nel
  momento in cui l'app va in background — proprio lo scenario "telefono
  sbloccato incustodito" dichiarato; fix con `FLAG_SECURE` sempre attivo in
  `MainActivity.java` (app-wide, indipendente dal toggle, blocca anche
  screenshot/registrazione schermo).
- **Non testato su device reale** (nessun emulatore/telefono disponibile in
  questo ambiente) — comportamento di heads-up notification, banner
  aggiornamento, prompt biometrico e `FLAG_SECURE` da verificare sul
  telefono dopo l'installazione della nuova APK.

**Fix 4.11.1**: upload di cessione del credito firmata e verbali di
uscita/rientro firmati non notificavano gli esterni assegnati alla pratica
(a differenza dell'upload di foto/documenti generici, che già passava per
`_notifica_esterni_assegnati`). I tre endpoint (`cessione_upload_signed` in
`routes.py`, `verbale_uscita_firmata`/`verbale_rientro_firmata` in
`routes_verbale.py`) ora chiamano la stessa funzione dopo il salvataggio.

Candidate non ancora costruite (nessuna traccia nel codice, verificato via
grep): matching ricevuta PEC InfoCert (accettazione/consegna), export
CSV/Excel elenco pratiche, backup notte automatico DB, filtri sulla home
admin (il portale esterno li ha già).
