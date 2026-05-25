# LYS Workflow Hub

Piattaforma di automazione documentale per la **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database di WinCar in sola lettura,
genera documenti precompilati (cessione del credito, richiesta risarcimento per atto
vandalico, ecc.), monitora le risposte delle compagnie assicurative via PEC ed email
ordinaria, classifica le risposte con un modello AI, produce bozze di replica e genera
alert mirati.

> Versione attuale: **0.4.0.dev1**

## Stato del progetto

| Milestone | Contenuto | Stato |
|----|----|----|
| **M1** | Fondazione + Workflow A (Cessione del credito) | ✅ completata |
| **M2** | Workflow B (Richiesta risarcimento vandalismo) — bozza PEC + anagrafica compagnie | ✅ completata |
| **M2bis** | Invio effettivo via SMTP della PEC (InfoCert SSL 465) + audit | ✅ completata |
| **M3** | Sottosistema posta in entrata + AI + Workflow C (lettura risposte) | ✅ completata |
| **M4** | Cruscotto bozze di risposta + context builder + firma cessione | ✅ completata |

---

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
        |   - risposte (M3/M4)        |
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
- **Notif** = dispatcher notifiche (email + push smartphone)

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
│   └── run_polling.py              Ciclo fetch→match→classify→notify
├── src/
│   └── lys_workflow_hub/
│       ├── main.py                 Entry point FastAPI
│       ├── config.py               Caricamento .env
│       ├── core/
│       │   ├── wincar_repository.py
│       │   ├── schema_check.py
│       │   ├── compagnie_repository.py
│       │   ├── mail_in_repository.py   Mail in arrivo (M3)
│       │   ├── pec_log_repository.py   Audit invii PEC
│       │   └── draft_repository.py     Bozze di risposta (M4)
│       ├── workflows/
│       │   ├── cessione_credito/       Workflow A — genera .docx/.pdf
│       │   │   └── assets/             Firma pre-apposta (PNG)
│       │   ├── risarcimento_vandalismo/ Workflow B — PEC vandalismo
│       │   └── risposte/               Workflow C — lettura + risposta
│       │       ├── matcher.py
│       │       ├── context_builder.py  Costruisce bozza risposta (M4)
│       │       └── body_generator.py   AI classification (M3)
│       ├── integrations/
│       │   ├── imap_fetcher.py
│       │   ├── pec_mailer.py
│       │   ├── ai_classifier.py
│       │   └── notifier.py
│       └── web/
│           ├── routes.py               Pratica + Workflow A
│           ├── routes_vandalismo.py    Workflow B
│           ├── routes_compagnie.py     CRUD compagnie
│           ├── routes_pec_log.py       Cronologia PEC
│           ├── routes_risposte.py      Lista/dettaglio risposte (M3)
│           └── routes_bozze.py         Cruscotto bozze (M4)
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
