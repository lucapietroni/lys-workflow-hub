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
│   └── verbale_cortesia/           Workflow D
│       ├── data.py                 VerbaleData dataclass + from_pratica()
│       ├── generator.py            DOCX uscita/rientro (logo LYS, tabelle bordate)
│       ├── archive.py              Salva PDF in Pratiche/<n>/Pubblici/Allegati/
│       └── assets/logo_lys.png     Logo LYS Auto Carrozzeria & Noleggio
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
    └── templates/ + static/
scripts/
├── run_polling.py                  Ciclo polling completo
└── create_admin.py                 Bootstrap primo utente admin
```

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
- **Prod**: `C:\LYSApp\lys-workflow-hub` (Windows), Task Scheduler
- **gh CLI**: `"/mnt/c/Program Files/GitHub CLI/gh.exe"` (non nel PATH WSL)
- **Python env**: `.venv` nella root del repo
- **DB**: `data/lys_hub.db` (gitignored)
- **Chiave API**: in `.env` (gitignored)

---

## Stato attuale

Versione **4.11.0**, tutto su branch `main`, in produzione su
`https://hub.lysauto.it`. Changelog per-commit in `git log`; le decisioni
tecniche non ovvie dal codice (formati, gotcha, cause di bug reali) restano
documentate nelle sezioni sopra, per sottosistema.

Candidate non ancora costruite (nessuna traccia nel codice, verificato via
grep): matching ricevuta PEC InfoCert (accettazione/consegna), export
CSV/Excel elenco pratiche, backup notte automatico DB, filtri sulla home
admin (il portale esterno li ha già).
