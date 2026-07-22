# LYS Workflow Hub — Contesto di sviluppo

> Branch: **v2** · Versione: **3.3.0** (base: v1.0.4 / main)

---

## Cos'è questo progetto

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl** (Roma).
Legge le pratiche dal gestionale **WinCar** (database Microsoft Access `.mdb`) in
sola lettura, genera documenti precompilati, monitora le risposte assicurative
via PEC/email, classifica con AI (Anthropic Claude), produce bozze di replica,
genera alert SLA. Branch v2 aggiunge i verbali di consegna/riconsegna veicoli di cortesia (v2.0),
il sistema di foto lavorazioni automatiche via Syncthing + Claude Vision (v2.1), e — a partire
dalla v3.0 — un sistema di login/ruoli in vista della pubblicazione dell'app su internet e di
un portale di collaborazione per agenzie pratiche auto / avvocati esterni.

**Stack**: FastAPI + Jinja2 · SQLite `lys_hub.db` · pyodbc → WinCar `.mdb` ·
InfoCert Legalmail (IMAP + SMTP SSL 465) · Anthropic Claude API · `pypdf` ·
python-docx + docx2pdf (Word COM) · watchdog (file system events)

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
    ├── Workflow D — Verbali cortesia [v2]        → python-docx → PDF via Word COM
    └── Workflow E — Foto lavorazioni [v2.1]      → watchdog → Claude Vision → file copy

Script polling (Task Scheduler)
    └── run_polling.py: fetch → match → classify → auto-transition → notify

Foto watcher (thread daemon, avviato al boot se FOTO_INBOX_PATH configurato)
    └── Syncthing inbox → targa via Claude Vision → fallback/<TARGA>/ + WinCar Pratiche/
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
- `foto_lavorazioni` — log foto processate dal watcher [v2.1]
- `utenti` — account applicativi: email, password_hash (bcrypt), ruolo (admin/esterno) [v3.0]

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
│   ├── foto_lavorazioni_repository.py  Log foto processate [v2.1]
│   └── utenti_repository.py        Utenti + autenticazione (bcrypt, lockout) [v3.0]
├── integrations/
│   ├── imap_fetcher.py             Fetch IMAP + estrazione body + PDF
│   ├── ai_classifier.py            Classificatore Anthropic Claude
│   ├── pdf_extractor.py            Estrazione testo PDF allegati (pypdf)
│   ├── pec_mailer.py               SMTP + IMAP append posta inviata
│   ├── notifier.py                 Push ntfy + email
│   └── foto_watcher.py             Watchdog + Claude Vision + routing foto [v2.1]
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
    ├── auth.py                     Sessione, AuthMiddleware, require_admin, CSRF [v3.0]
    ├── routes_auth.py              GET/POST /login, POST /logout [v3.0]
    ├── routes.py                   Pratica + Workflow A (admin-only)
    ├── routes_vandalismo.py        Workflow B (admin-only)
    ├── routes_risposte.py          Cruscotto risposte (admin-only)
    ├── routes_bozze.py             Cruscotto bozze (admin-only)
    ├── routes_verbale.py           Workflow D [v2] — 6 route (admin-only)
    ├── routes_foto.py              Workflow E [v2.1] — log foto /foto (admin-only)
    ├── routes_compagnie.py         CRUD compagnie (admin-only)
    ├── routes_impostazioni.py      Statistiche + policy editor (admin-only)
    └── templates/ + static/
scripts/
├── run_polling.py                  Ciclo polling completo
└── create_admin.py                 Bootstrap primo utente admin [v3.0]
```

---

## Workflow D — Verbali cortesia [v2]

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

## Workflow E — Foto lavorazioni [v2.1, migliorato in v2.2]

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

## Foto e documenti in pratica [v2.2]

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

## Autenticazione [v3.0 fase 1]

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
(bcrypt), `ruolo` (`admin` | `esterno`), `attivo`, `failed_login_count` +
`locked_until` per il blocco anti-bruteforce. Due ruoli fissi per ora (non
tabella permessi granulare) — [[decisione utente]]: se in futuro serve più
granularità si aggiunge senza toccare lo schema base.

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

## Assegnazione pratiche [v3.0 fase 3]

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

## Note e calendario condivisi [v3.0 fase 4]

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

## Notifiche di collaborazione + prossimi appuntamenti [v3.0 fase 5, parte A+C]

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
a `notify_admin_nuova_attivita`/`notify_esterno_nuova_attivita`, le due
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

### Fase 5 parte B (non ancora costruita): reminder schedulati
Reminder "il giorno prima" con lead time configurabile per evento, inviati
via uno script schedulato (stesso pattern di `run_polling.py` — Task
Scheduler, non un processo in background nell'app) più una tabella di dedup
(come `pec_sla_reminder`) per non ri-notificare lo stesso evento ad ogni
esecuzione. Rimandata a un secondo step per non richiedere subito una nuova
voce Task Scheduler sul PC carrozzeria.

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

## Versioni

| Versione | Branch | Contenuto |
|----------|--------|-----------|
| 1.0.4 | main | Base stabile: cessione, vandalismo, risposte AI, bozze, SLA, UI dark glass, allegati email, fix re-download |
| 2.0.0 | v2 | + Verbali cortesia con auto di cortesia DB, dichiarazione necessità, timbro LYS |
| 2.1.0 | v2 | + Foto lavorazioni: Syncthing + watchdog + Claude Vision → routing automatico per targa |
| 2.2.0 | v2 | + Foto/documenti in pratica (anteprima inline) + lettura targa a due passaggi (locate+zoom), toggle copia-pratica, fix orario UTC |
| 3.0.0 | v2 | + Autenticazione fase 1: utenti/ruoli, login/logout, sessione cookie, route admin-only, lockout anti-bruteforce, bootstrap CLI. Fase 2: reverse proxy + TLS (Caddy), app pubblicata su `hub.lysauto.it` |
| 3.1.0 | v2 | + Assegnazione pratiche fase 3: UI gestione utenti (`/utenti`), assegnazione pratiche many-to-many (`pratica_assegnazioni`), portale esterno di sola lettura (`/portale`), nav condizionale per ruolo |
| 3.2.0 | v2 | + Note e calendario condivisi fase 4: thread note (`pratica_note`) e calendario (`pratica_eventi`) tra admin e collaboratori esterni, su `/pratiche/{numero}` e nuova `/portale/pratiche/{numero}` (dettaglio completo esterno: WinCar + note + calendario), fix redirect post-login esterno (`/portale` invece di `/`, admin-only) |
| 3.3.0 | v2 | + Notifiche collaborazione fase 5 (parte A+C): push admin/email esterno in tempo reale su nuova nota/evento, widget "Prossimi appuntamenti" su home e `/portale`, `Settings.public_url()`/`PUBLIC_BASE_URL` per link corretti fuori LAN nelle notifiche |

---

## TODO v2

- Deploy v2.2 su prod (dopo test completo su dev)
  - `pip install -r requirements.txt` (installa watchdog + Pillow, già in requirements.txt)
  - Verificare `ANTHROPIC_VISION_MODEL` in `.env` prod (default claude-sonnet-5 se assente)
  - Setup Syncthing smartphone → PC (se non già fatto)
- Sezione danni verbali: UI grafica schema auto cliccabile
- Franchigie verbali: definire valori default LYS Auto
- **v3.0 fase 2** completata — app raggiungibile su `https://hub.lysauto.it`
  (dettagli/gotcha firewall in sezione "Autenticazione" sopra). CSRF esteso
  a tutti i form (oggi solo sul login) resta debito tecnico separato.
- **v3.0 fase 3** completata — vedi sezione "Assegnazione pratiche".
- **v3.0 fase 4** completata — vedi sezione "Note e calendario condivisi".
- **v3.0 fase 5 parte A+C** completata — vedi sezione "Notifiche di
  collaborazione + prossimi appuntamenti". Ricorda di impostare
  `PUBLIC_BASE_URL` in `.env` prod perché i link nelle notifiche funzionino
  fuori LAN.
- **v3.0 fase 5 parte B** (non ancora costruita): reminder schedulati "il
  giorno prima" (es. "domani c'è una perizia"), richiede una nuova voce
  Task Scheduler sul PC carrozzeria.
- Dopo deploy v3.0 in prod: lanciare `scripts/create_admin.py` per creare il
  primo utente admin, e impostare `SECRET_KEY` in `.env` prod
