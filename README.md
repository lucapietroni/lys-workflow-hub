# LYS Workflow Hub

Piattaforma di automazione documentale per la **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database di WinCar in sola lettura,
genera documenti precompilati (cessione del credito, richiesta risarcimento per atto
vandalico, ecc.), monitora le risposte delle compagnie assicurative via PEC ed email
ordinaria, classifica le risposte con un modello AI, produce bozze di replica e genera
alert mirati.

> Versione attuale: **0.7.0**

## Stato del progetto

| Milestone | Contenuto | Stato |
|----|----|----|
| **M1** | Fondazione + Workflow A (Cessione del credito) | ✅ completata |
| **M2** | Workflow B (Richiesta risarcimento vandalismo) — bozza PEC + anagrafica compagnie | ✅ completata |
| **M2bis** | Invio effettivo via SMTP della PEC (InfoCert SSL 465) + audit | ✅ completata |
| **M3** | Sottosistema posta in entrata + AI + Workflow C (lettura risposte) | ✅ completata |
| **M4** | Cruscotto bozze di risposta + context builder + firma cessione | ✅ completata |
| **M4.1** | Bug fix bozza risposta: subject annidato + compagnia mancante in PEC inviate | ✅ completata |
| **M5** | Stato pratica + SLA tracker + statistiche compagnie | ✅ completata |
| **M5.1** | Dashboard KPI espansa + pagina statistiche per compagnia | ✅ completata |
| **M5.2** | Policy editor bozze da UI — senza riavvio app | ✅ completata |
| **M5.3** | Estrazione testo da allegati PDF nelle risposte assicurative | ✅ completata |
| **M6.1** | Escalation SLA automatica — sollecito / formale / diffida | ✅ completata |
| **M7** | Fix qualità classificazione AI + robustezza cruscotto risposte + UX allegati | ✅ completata |

---

### Cosa fa M7 oggi

#### Classificazione AI — fix diniego/polizza non trovata

- **Bug risolto**: le risposte di compagnia che comunicano "il veicolo non
  risulta assicurato con noi" o "polizza non trovata" venivano classificate
  come `liquidazione` (85% confidence) perché la definizione della categoria
  includeva "il diniego di copertura". Conseguenza: lo stato pratica veniva
  automaticamente portato a *In liquidazione*, il che era errato.
- **Fix al prompt AI** (`ai_classifier.py`): `liquidazione` ora richiede
  esplicitamente un importo concreto o una conferma di pagamento. La categoria
  `altro` include ora esempi espliciti di diniego con nota
  **"I dinieghi NON sono liquidazioni"**.
- **Soglia confidence 0.70** per le auto-transizioni di stato pratica
  (`run_polling.py`): transizioni con confidence < 0.70 vengono skippate con
  log esplicito. Defense-in-depth: blocca transizioni errate anche in caso di
  classificazioni ambigue future.

#### Estrazione PDF allegati PEC — fix robustezza (M5.3 follow-up)

- **Bug risolto**: `augment_body_with_pdf` riceveva il messaggio PEC esterno
  (wrapper InfoCert) invece del messaggio interno `postacert.eml`. Il PDF
  allegato dalla compagnia era nell'inner message e non veniva mai trovato.
  Fix: `_find_postacert(msg)` viene chiamato una sola volta in `fetch_into`
  e l'`inner_msg` viene passato sia a `_has_attachments` sia a
  `augment_body_with_pdf`.
- **Rimossa logica condizionale errata**: la vecchia implementazione estraeva
  il PDF solo se `len(body_text) < min_body_len` (200 caratteri). Una risposta
  con testo quotato della PEC originale — anche senza contenuto utile — faceva
  saltare l'estrazione. Ora il PDF viene estratto e appeso al corpo
  **sempre**, indipendentemente dalla lunghezza del body.
- **Endpoint `reextract_body`** pubblico in `imap_fetcher.py`: permette
  al route "Riclassifica" di rileggere il `.eml` dal filesystem e riestrarne
  il corpo con la logica corrente (fix PDF incluso).

#### Cruscotto risposte — nuove azioni

- **Pulsante "🔄 Riclassifica"** nella pagina risposta: re-legge il file
  `.eml` dal filesystem (applica tutti i fix PDF), aggiorna `body_text` in DB,
  cancella la classificazione esistente e la rigenera con AI. Utile per
  correggere classificazioni salvate prima dei fix.
- **Pulsante "🗑 Elimina"** con conferma inline: rimuove la mail dal
  cruscotto senza cancellare il file `.eml` su filesystem.
- **Soft delete** (`ignorata = 1`): le mail eliminate non vengono mai
  ri-scaricate al ciclo di polling successivo perché l'UID rimane in DB e
  `max_uid()` non scende mai. Nessun tombstone separato necessario.
- **Flash banner** dopo riclassificazione riuscita (`?riclassificata=1`).

#### UX allegati — anteprima PDF pagina vandalismo

- Il pulsante 📄 sugli allegati PDF nella pagina di creazione richiesta
  vandalismo apre ora il file in **nuova scheda del browser** (identico al
  comportamento già presente in `bozza_edit.html`). Rimosso il precedente
  modal `<dialog>` + `<embed>` che causava una finestra vuota sovrapposta.
  `stopPropagation` mantenuto per evitare il toggle involontario del
  checkbox allegato nella `<label>` parent.

---

### Cosa fa M6.1 oggi

- **Escalation SLA a tre livelli**: quando una PEC inviata supera le soglie
  configurate senza ricevere risposta, il sistema genera automaticamente una
  bozza PEC pre-compilata di escalation:
  - **Livello 1 — Sollecito** (`SLA_GIORNI_ALERT`, default 15 gg): tono
    cortese, ricorda la comunicazione originale.
  - **Livello 2 — Sollecito formale** (`SLA_FORMALE_GIORNI`, default 30 gg):
    tono più urgente, fissa un termine di 15 giorni prima di procedere.
  - **Livello 3 — Diffida formale** (`SLA_DIFFIDA_GIORNI`, default 45 gg):
    atto di messa in mora, cita gli artt. 148 ss. Codice delle Assicurazioni.
- **Bozze in `/bozze`**: ogni sollecito appare nella sezione "Solleciti SLA"
  con badge colorato (giallo/arancione/rosso per livello). L'operatore apre
  l'editor, rivede/modifica il testo, e invia con un click.
- **Idempotenza**: la coppia `(pec_id, livello)` è UNIQUE in DB — il ciclo
  di polling non crea duplicati anche se eseguito più volte con lo stesso
  stato.
- **Tracking livelli**: `pec_sla_reminder.livello` registra quale livello è
  già stato inviato; il ciclo controlla solo i livelli non ancora gestiti.
- **Override per compagnia** (opzionale): le colonne
  `sla_sollecito_giorni`, `sla_formale_giorni`, `sla_diffida_giorni` in
  `compagnie_assicurative` permettono soglie personalizzate per compagnie
  storicamente lente.
- **Push ntfy** con livello nel titolo ("SLA — Diffida formale: pratica 1234")
  così lo smartphone mostra subito la gravità senza aprire l'app.

### Cosa fa M5 oggi

- **Stato pratica** `/pratiche/{n}`: ogni pratica ha un ciclo di vita tracciato
  in `lys_hub.db` (aperta → in gestione → perito nominato → in liquidazione → chiusa).
  L'operatore aggiorna lo stato manualmente dal widget nella pagina pratica, con nota
  opzionale. Lo stato è storicizzato (storia immutabile, no UPDATE).
- **Transizioni automatiche**: quando il ciclo di polling classifica una risposta,
  lo stato avanza automaticamente — `presa_in_carico` → in gestione,
  `nomina_perito` → perito nominato, `liquidazione` → in liquidazione.
  Non scende mai di livello, non transita mai da "chiusa".
- **SLA tracker**: ogni ciclo di polling verifica le PEC inviate senza risposta
  oltre la soglia configurata (`SLA_GIORNI_ALERT`, default 15 giorni). Per ogni
  breach manda un push ntfy.sh con link diretto alla pratica. Cooldown integrato:
  non rispamma se il reminder è già stato inviato di recente. Log in
  `pec_sla_reminder`.
- **KPI home espansa**: la hero strip ora mostra tre contatori — risposte da
  gestire, bozze in attesa, **SLA scaduti** — tutti in rosso se > 0.
- **Banner SLA** sulla pagina pratica: se la PEC di quella pratica è in breach,
  mostra quanti giorni sono passati e verso quale compagnia.

### Cosa fa M5.1 oggi

- **Pagina `/statistiche`**: KPI globali (PEC inviate totali, risposte ricevute,
  costo AI mese/totale, pratiche con stato) + tabella aggregata per compagnia
  (PEC inviate, risposte, % risposta, giorni medi, breakdown per categoria,
  costo AI). Dati calcolati live da query SQL su `lys_hub.db`.

### Cosa fa M5.2 oggi

- **Pagina `/impostazioni`**: editor visuale della policy di generazione bozze
  per ogni categoria AI. Ogni categoria può essere impostata a:
  *Bozza automatica*, *Solo su richiesta*, *Nessuna bozza*.
- **Policy persistita in SQLite** (`categoria_policy`): la modifica da UI
  è attiva al prossimo ciclo polling senza riavviare l'app. Fallback ai
  default hardcoded se la tabella non è disponibile.
- **Wire completo**: `crea_bozza_se_serve()` accetta `policy_override` dal DB;
  il polling carica il dict una volta per ciclo e lo propaga a tutta la pipeline.

### Cosa fa M5.3 oggi

- **Estrazione testo da PDF allegati**: molte compagnie assicurative inviano la
  risposta reale (presa in carico, nomina perito, liquidazione) come PDF allegato
  invece di scriverla nel corpo dell'email. Il body rimane un pro-forma generico
  tipo "Si veda l'allegato" o addirittura vuoto.
- **Logica**: durante il fetch IMAP il fetcher estrae il testo da **tutti** i PDF
  allegati (anche se il corpo è già lungo — la compagnia può scrivere qualsiasi
  cosa nel body e allegare la risposta reale) tramite `pypdf` (puro Python, niente
  Ghostscript) e lo appende al corpo. Massimo 3 PDF per mail, 4000 caratteri per
  PDF, totale troncato a 8000 caratteri.
- **Struttura PEC InfoCert**: il fetcher cerca prima il messaggio interno
  `postacert.eml` (wrapper InfoCert) per individuare gli allegati reali; se non
  presente usa il messaggio diretto.
- **Prefisso contestuale**: il testo estratto è prefissato da
  `[ALLEGATO PDF: <nome>]` così il classificatore AI sa che il contenuto viene
  da un allegato.
- **Degradazione silenziosa**: se `pypdf` non è installato o il PDF è corrotto/
  protetto/solo-immagini, la funzione restituisce stringa vuota senza bloccare
  il polling.
- **Configurabile** via `.env`: `PDF_EXTRACT_ENABLED=true`.

### Cosa fa M4 oggi

- **Cruscotto bozze** `/bozze`: lista filtrabile delle bozze di risposta alle compagnie
  (in attesa, pronte, inviate, annullate) con badge per stato e pratica.
- **Editor bozza**: pagina di modifica corpo PEC + selezione allegati + destinatario,
  con anteprima allegati cliccabile prima dell'invio.
- **Generazione automatica** della bozza nel ciclo di polling: quando arriva una
  risposta classificata come "action required", il sistema costruisce in automatico
  il testo di replica contestualizzato (context builder).
- **Invio PEC** direttamente dall'editor (via SMTP InfoCert, dry-run da `.env`).
- **Firma pre-apposta** sul documento Cessione del Credito: l'immagine della firma
  Lys Auto viene inserita automaticamente sotto il campo "Firma Cessionario" nel PDF
  generato, così il documento è pronto senza intervento manuale.
- **Opt-in manuale**: dalla pagina risposta è possibile forzare la generazione di una
  bozza per qualsiasi mail, anche se non action-required.

### Cosa fa M3 oggi

- **Fetch IMAP** incrementale (per UID) dalle caselle PEC (InfoCert Legalmail)
  e ordinaria (Tophost). Le mail vengono archiviate come `.eml` grezzi in
  `C:\LYSApp\Mail_in\<anno>\<casella>\`.
- **Matching automatico** della risposta alla PEC inviata di partenza:
  prima header `In-Reply-To`/`References` (confidence 1.0), poi euristica
  su oggetto+body cercando targa/pratica/polizza (confidence 0.6–0.9).
- **Classificatore AI** (Anthropic Claude Haiku 4.5) in 5 categorie:
  presa in carico, nomina perito, richiesta documenti, liquidazione, altro.
  Estrae anche key facts (numero sinistro, importo, perito, scadenza).
  Output JSON strutturato + tracking costo per chiamata.
- **Notifiche**: push istantaneo via ntfy.sh per ogni risposta "da gestire"
  + email riassuntiva di fine ciclo all'indirizzo configurato.
- **Script polling** schedulabile (Task Scheduler) che esegue
  fetch → match → classify → notify in un singolo ciclo. Lock file
  per evitare esecuzioni sovrapposte.
- **UI**: pagina `/risposte` con lista filtrabile + dettaglio con
  classificazione, key facts, link alla pratica e alla PEC originale.
  Banner sulla pagina pratica con le risposte "action_required" pendenti.

### Cosa fa M2-bis oggi

- Pagina di **conferma pre-invio** con riepilogo: destinatario PEC, oggetto,
  corpo completo, allegati con dimensione totale, eventuali warning (modalità
  dry-run attiva, campi mancanti).
- **Invio reale** via `smtplib` su SMTP_SSL porta 465 (default
  InfoCert/Legalmail). Fallback automatico a STARTTLS se la porta è 587.
- **Modalità dry-run** attivabile via `.env` (`PEC_DRY_RUN=true`): genera
  comunque il `.eml` e lo archivia ma non apre connessione SMTP.
- **Archiviazione `.eml`** in `C:\LYSApp\PEC_inviate\<anno>\` con nome
  deterministico (timestamp + pratica + compagnia).
- **DB SQLite `pec_inviate`** con record di ogni invio (data, destinatario,
  oggetto, message-id, percorso file, esito, eventuale errore).
- Pagine **`/pec-inviate`** (cronologia) e **`/pec-inviate/{id}`** (dettaglio
  + download del file `.eml`).
- Banner nella pagina vandalismo "PEC già inviata il …" quando ci sono
  invii precedenti per la stessa pratica.

### Cosa fa M2 oggi

- Lettura della pratica WinCar in sola lettura, come per M1.
- Scansione automatica delle cartelle WinCar della pratica:
  `Pratiche\<n>\Pubblici\Foto\` (foto del danno) e
  `Pratiche\<n>\Pubblici\Allegati\` (denuncia, cessione firmata, documenti).
- Anagrafica interna delle **compagnie assicurative** (SQLite, CRUD da UI):
  PEC, email, indirizzo postale, ufficio sinistri, note.
- Matching automatico fra il nome compagnia letto da WinCar (`F_DEASCL`) e
  l'anagrafica interna (normalizzazione: case, spazi, suffissi tipo S.p.A./srl).
- Schermata di anteprima editabile della **bozza PEC**: oggetto + corpo
  testuale completo (assicurato, polizza, veicolo, evento, denuncia,
  cessione, elenco numerato allegati, richiesta di nomina perito, contatti
  carrozzeria). L'operatore seleziona via checkbox quali allegati elencare.

---

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
        |   - risposte (M3/M4/M5)     |
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
- **Doc** = motore documenti (python-docx + Word COM per PDF)
- **Mail** = lettore/mittente posta (IMAP + SMTP/PEC)
- **AI** = classificatore di risposte assicurative (Claude API)
- **Notif** = dispatcher notifiche (email + push smartphone + SLA alert)

## Requisiti

- **Sistema operativo:** Windows 10 / 11 (il driver Microsoft Access è Windows-only).
- **Python:** 3.11 o superiore (64-bit).
- **Driver Microsoft Access Database Engine 2016 Redistributable** (64-bit) — gratuito,
  scaricabile dal sito Microsoft.
- **Microsoft Word** (per la conversione `.docx` → PDF via `docx2pdf` + COM).
- **WinCar** installato sullo stesso PC dove gira l'app (per accesso diretto ai `.mdb`).

## Installazione

```bash
# 1. Clone
git clone https://github.com/lysauto/lys-workflow-hub.git
cd lys-workflow-hub

# 2. Virtualenv
python -m venv .venv
.venv\Scripts\activate

# 3. Dipendenze
pip install -r requirements.txt
pip install -e .

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
├── README.md
├── .gitignore
├── .env.example                    Template di configurazione (sicuro)
├── requirements.txt                Dipendenze Python (runtime + dev)
├── pyproject.toml                  Metadati pacchetto + dipendenze core
├── start_lys.bat                   Script avvio produzione (pythonw)
├── run_polling.bat                 Script polling mail (Task Scheduler)
├── docs/
│   ├── Analisi_LYS_Workflow_Hub_v2.docx
│   ├── Decisioni_finalizzate_v2.docx
│   └── SETUP_PRODUCTION.md         Guida installazione PC carrozzeria
├── scripts/
│   ├── dump_schema_wincar.py
│   ├── run_polling.py              Ciclo fetch→match→classify→notify+SLA
│   └── reset_polling.py            Utility reset UID IMAP
├── src/
│   └── lys_workflow_hub/
│       ├── main.py                 Entry point FastAPI
│       ├── config.py               Caricamento .env
│       ├── core/
│       │   ├── wincar_repository.py
│       │   ├── schema_check.py
│       │   ├── compagnie_repository.py
│       │   ├── mail_in_repository.py       Mail in arrivo (M3)
│       │   ├── pec_log_repository.py       Audit invii PEC
│       │   ├── draft_repository.py         Bozze di risposta (M4)
│       │   ├── pratica_stato_repository.py Stato pratica + SLA (M5)
│       │   ├── categoria_policy_repository.py Policy bozze in DB (M5.2)
│       │   └── sollecito_repository.py     Solleciti SLA (M6.1)
│       ├── workflows/
│       │   ├── cessione_credito/       Workflow A — genera .docx/.pdf
│       │   │   └── assets/             Firma pre-apposta (PNG)
│       │   ├── risarcimento_vandalismo/ Workflow B — PEC vandalismo
│       │   └── risposte/               Workflow C — lettura + risposta
│       │       ├── matcher.py
│       │       ├── context_builder.py  Costruisce bozza risposta (M4)
│       │       ├── categorie_policy.py Policy statiche (fallback M5.2)
│       │       ├── sollecito_generator.py  Testi escalation SLA (M6.1)
│       │       └── body_generator.py   AI classification (M3)
│       ├── integrations/
│       │   ├── imap_fetcher.py
│       │   ├── pec_mailer.py
│       │   ├── ai_classifier.py
│       │   ├── pdf_extractor.py        Estrazione testo PDF allegati (M5.3)
│       │   └── notifier.py
│       └── web/
│           ├── routes.py               Pratica + Workflow A
│           ├── routes_vandalismo.py    Workflow B
│           ├── routes_compagnie.py     CRUD compagnie
│           ├── routes_pec_log.py       Cronologia PEC
│           ├── routes_risposte.py      Lista/dettaglio risposte (M3)
│           ├── routes_bozze.py         Cruscotto bozze (M4)
│           └── routes_impostazioni.py  Statistiche + policy editor (M5)
├── tests/
└── data/                           DB SQLite locale (gitignored)
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
- File di test locali (`test_*.docx`, `test_page_*.png`).

**Prima di ogni `git push` verifica con `git status` che nessun file con dati reali
sia in stage.**

## Licenza

Codice proprietario sviluppato per Carrozzeria LYS Auto srl. Nessuna ridistribuzione
senza autorizzazione esplicita.

## Contatti progetto

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
