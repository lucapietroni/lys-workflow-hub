# LYS Workflow Hub

Piattaforma di automazione documentale per **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database WinCar in sola lettura,
genera documenti precompilati, monitora le risposte delle compagnie assicurative
via PEC/email, classifica le risposte con AI e genera alert mirati.

> Branch attivo: **v2** · Versione: **3.10.0**
> Branch stabile: **main** (v1.0.4)

---

## Rami di sviluppo

| Branch | Versione | Stato |
|--------|----------|-------|
| `main` | **1.0.4** | Stabile — funzionalità assicurative complete + allegati email + fix re-download |
| `v2` | **3.10.0** | Sviluppo attivo — verbali cortesia + foto lavorazioni automatiche + foto/documenti in pratica (vista + upload dal portale esterno) + autenticazione/pubblicazione internet + assegnazione pratiche + note/calendario condivisi + notifiche collaborazione (real-time + self-service + reminder schedulati) + stato pratica nel portale (vista + modifica) + CSRF su tutti i form + calendario mensile + UI responsive tablet/telefono |

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
- **Ruoli**: `admin` (accesso completo) ed `esterno` (accesso limitato alle
  sole pratiche assegnate — vedi "Novità v3.1" sotto).
- **Anti-bruteforce**: blocco account dopo 5 tentativi falliti consecutivi
  (configurabile), sblocco automatico dopo 15 minuti.
- **Bootstrap**: nessuna self-registration — `scripts/create_admin.py` crea
  il primo utente admin dopo il deploy.
- **`SECRET_KEY` obbligatoria in produzione**: l'app non si avvia senza,
  per evitare sessioni firmate con una chiave debole/assente.

**Fase 2 — reverse proxy + TLS: completata.** App raggiungibile da internet
su `https://hub.lysauto.it` (Caddy + Let's Encrypt sul PC carrozzeria,
guida in `docs/SETUP_PRODUCTION.md` §10 + `deploy/Caddyfile`).

---

## Novità v3.1 (fase 3) — Assegnazione pratiche e portale esterno

- **Gestione utenti via UI** (`/utenti`, admin-only): crea/modifica/disattiva
  utenti admin ed esterni, resetta password — non serve più la riga di
  comando per tutto tranne il primissimo admin (`scripts/create_admin.py`).
  Protezione integrata: non è possibile disattivare, retrocedere o eliminare
  l'ultimo amministratore attivo rimasto.
- **Assegnazione pratiche**: su `/pratiche/<n>`, card "Collaboratori
  esterni" — l'admin assegna la pratica a uno o più utenti esterni (es.
  agenzia pratiche auto **e** avvocato insieme sulla stessa pratica).
- **Portale esterno** (`/portale`): l'utente esterno vede l'elenco delle
  proprie pratiche assegnate (numero, cliente, veicolo, data sinistro);
  cliccando il numero apre il dettaglio completo — vedi "Novità v3.2" sotto.
- **Navigazione condizionale per ruolo**: un utente esterno vede solo "Le
  mie pratiche" in menu, non le voci admin (che comunque risponderebbero
  403 se aperte direttamente).

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.2 (fase 4) — Note e calendario condivisi

- **Note condivise per pratica**: thread unico (non un canale per utente) tra
  admin e collaboratori esterni assegnati — es. "preso app.to con perito",
  "servono foto lavorazione", "serve preventivo". Visibile e scrivibile su
  `/pratiche/<n>` (admin) e `/portale/pratiche/<n>` (esterno assegnato).
- **Calendario condiviso per pratica**: appuntamenti (es. data della perizia),
  aggiungibili/eliminabili da chiunque abbia accesso alla pratica.
- **Dettaglio pratica nel portale esterno** (`/portale/pratiche/<n>`): non
  più solo un elenco — l'esterno assegnato vede ora cliente, veicolo,
  sinistro, controparte, foto, documenti, note e calendario della pratica,
  in sola lettura per i dati WinCar e in scrittura per note/calendario.
  Un esterno non assegnato riceve 404 (non 403, per non rivelare l'esistenza
  della pratica).
- **Fix redirect post-login**: un utente esterno senza pratiche precedenti
  finiva su `/` dopo il login (route admin-only → 403). Ora atterra su
  `/portale`; gli admin continuano ad atterrare su `/`.

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.3 (fase 5, parte A+C) — Notifiche di collaborazione

- **Notifiche in tempo reale**: quando un esterno scrive una nota o aggiunge
  un evento, l'admin riceve una push sul telefono (ntfy.sh, stesso canale
  già usato per gli alert PEC). Quando l'admin scrive una nota o aggiunge un
  evento su una pratica con collaboratori assegnati, ciascun esterno
  assegnato riceve un'email.
- **"Prossimi appuntamenti"**: nuovo widget su home (admin, tutte le
  pratiche) e su `/portale` (esterno, solo le proprie pratiche assegnate) —
  eventi di calendario nei prossimi 7 giorni, calcolato al caricamento
  pagina, nessuno scheduler richiesto.
- **`PUBLIC_BASE_URL`** (nuova variabile `.env`): i link nelle notifiche
  push/email ora puntano all'URL pubblico dell'app (`https://hub.lysauto.it`)
  invece che a `http://APP_HOST:APP_PORT` (utilizzabile solo da dentro la
  LAN) — impostala in produzione perché i link funzionino dal telefono o
  in un'email a un esterno.

Fase successiva (non ancora costruita): reminder schedulati "il giorno
prima" per gli eventi di calendario (richiede una nuova voce Task Scheduler
sul PC carrozzeria, stesso pattern di `run_polling.py`). Dettagli tecnici
completi in `CONTEXT.md`.

---

## Novità v3.4 (fase 5, parte D) — Notifiche self-service

- **`/portale/impostazioni`**: ogni utente esterno sceglie autonomamente se
  ricevere email e/o push quando l'admin aggiorna una pratica assegnata
  (prima erano sempre attive, non disattivabili).
- **Push personale per gli esterni**: prima solo l'admin aveva un topic
  ntfy.sh configurato (`.env`); ora ogni esterno può impostare il proprio
  topic personale e ricevere notifiche push sul telefono.

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.5 (fase 5, parte E) — Stato pratica nel portale

- **Colonna "Stato"** nell'elenco `/portale`: badge colorato (stessi colori
  già usati su `/pratiche/<n>`), default "Aperta" se non ancora impostato.
- **Pratiche chiuse evidenziate**: riga visivamente attenuata, per
  distinguerle a colpo d'occhio dalle pratiche ancora attive.

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.6 (fase 5, parte F+G) — Stato modificabile dall'esterno + CSRF ovunque

- **L'esterno assegnato può cambiare lo stato pratica**, non solo vederlo:
  stesso widget dell'admin (dropdown + note) su `/portale/pratiche/<n>`.
  Ogni cambio notifica l'admin via push.
- **Nuovo stato "Periziata"**, tra "Perito nominato" e "In liquidazione".
- **Collaboratori esterni a tendina** su `/pratiche/<n>`: sezione collassata
  di default, si apre al click sul titolo.
- **CSRF esteso a tutti i form** (prima solo il login): ogni `POST`
  autenticato verifica un token legato alla sessione. Debito tecnico
  segnalato e rimandato a più riprese, chiuso in questa fase.

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.7 (fase 5, parte B + H) — Reminder schedulati, calendario mensile, note editabili

- **Reminder "il giorno prima"** (fase 5, parte B — l'ultima rimasta della
  roadmap v3.0): nuovo script schedulato `scripts/send_event_reminders.py`
  (Task Scheduler, una volta al giorno — guida in
  `docs/SETUP_PRODUCTION.md` §5.6), avvisa admin ed esterni assegnati per
  ogni appuntamento di calendario in scadenza domani, con dedup interno
  (non rispedisce lo stesso reminder due volte).
- **Modifica/eliminazione note** (solo admin): ogni nota su `/pratiche/<n>`
  ha ora un link "Modifica" (inline) e un pulsante "Elimina".
- **Pagina "Calendario"** (`/calendario` admin, `/portale/calendario`
  esterno): vista mensile stile Google Calendar con tutti gli appuntamenti
  — l'admin vede tutte le pratiche, l'esterno solo le proprie assegnate.
  Navigazione mese precedente/successivo, link diretto alla pratica.
- **Home admin**: la sezione "Suggerimenti rapidi" (statica) è sostituita
  dalle **ultime 20 pratiche aperte**, quando non c'è una ricerca in corso.

Dettagli tecnici completi in `CONTEXT.md`.

---

## Novità v3.8 — Widget appuntamenti arricchito + UI responsive

- **Widget "Prossimi appuntamenti"** (home admin e `/portale`): ora mostra
  anche cliente e targa della pratica, non solo titolo evento — es. "Perito
  De Santis — ROSSI MARIO — AB123CD — Pratica nr. 827" (numero cliccabile).
- **UI responsive su tablet/telefono**: la barra di navigazione (11 voci
  lato admin) ora scorre in orizzontale invece di rompere il layout della
  pagina; le tabelle senza colonne nascoste (compagnie, utenti, risposte)
  scorrono anche loro invece di allargare la pagina; i form inline (nuova
  nota/evento, assegna collaboratore) vanno a capo invece di restringersi
  fino a diventare illeggibili.

Dettagli tecnici completi in `CONTEXT.md`.

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
│   ├── utenti_repository.py        Utenti + autenticazione (bcrypt, lockout) [v3.0]
│   ├── pratica_assegnazioni_repository.py  Assegnazione pratiche↔utenti [v3.1]
│   ├── pratica_note_repository.py  Note condivise per pratica [v3.2]
│   └── pratica_eventi_repository.py  Calendario condiviso per pratica [v3.2]
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
    ├── routes_utenti.py            CRUD utenti /utenti (admin-only) [v3.1]
    ├── routes_portale.py           Portale esterno /portale + dettaglio + note/calendario (NON admin-only) [v3.1/v3.2]
    ├── routes.py                   Pratica + Workflow A + assegnazione + note/calendario (admin-only)
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
si avvia senza. L'esposizione su internet passa da Caddy (reverse proxy +
TLS, fase 2): la porta 8000 dell'app non è mai raggiungibile direttamente
dall'esterno, solo 443/80 verso Caddy.

## Licenza

Codice proprietario — Carrozzeria LYS Auto srl. Nessuna ridistribuzione senza
autorizzazione esplicita.

## Contatti

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
