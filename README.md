# LYS Workflow Hub

Piattaforma di automazione documentale per la **Carrozzeria LYS Auto srl**, integrata
con il gestionale **WinCar**. Legge le pratiche dal database di WinCar in sola lettura,
genera documenti precompilati (cessione del credito, richiesta risarcimento per atto
vandalico, ecc.), monitora le risposte delle compagnie assicurative via PEC ed email
ordinaria, classifica le risposte con un modello AI e produce alert mirati.

> Versione attuale: **0.2.0 — Workflow A (cessione) + Workflow B (vandalismo, bozza PEC)**

## Stato del progetto

| Milestone | Contenuto | Stato |
|----|----|----|
| **M1** | Fondazione + Workflow A (Cessione del credito) | completata |
| **M2** | Workflow B (Richiesta risarcimento vandalismo) — bozza PEC + anagrafica compagnie | completata |
| **M2bis** | Invio effettivo via SMTP della PEC (InfoCert SSL 465) + audit | completata |
| **M3** | Sottosistema posta in entrata + AI + Workflow C (lettura risposte) | pianificato |

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
- Pulsanti: **Copia corpo PEC** (negli appunti) e **Scarica bozza .txt**
  (file di testo con destinatario, oggetto e percorsi assoluti degli
  allegati da agganciare nel client PEC).

### Cosa NON fa ancora M2/M2-bis

- La lettura delle PEC in arrivo e la classificazione AI delle risposte
  delle compagnie (presa in carico, nomina perito, richiesta documenti,
  liquidazione): rimandato a M3.

Vedi `docs/Analisi_LYS_Workflow_Hub_v2.docx` per il documento di analisi completo
e `docs/Decisioni_finalizzate_v2.docx` per il riepilogo delle decisioni di progetto.

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
        |   - (futuri)                |
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
- **Doc** = motore documenti (python-docx + LibreOffice)
- **Mail** = lettore posta (IMAP/TLS)
- **AI** = classificatore di risposte assicurative (Claude API)
- **Notif** = dispatcher notifiche (email + push smartphone)

## Requisiti

- **Sistema operativo:** Windows 10 / 11 (il driver Microsoft Access è Windows-only).
- **Python:** 3.11 o superiore (64-bit).
- **Driver Microsoft Access Database Engine 2016 Redistributable** (64-bit) — gratuito,
  scaricabile dal sito Microsoft.
- **LibreOffice** (per la conversione `.docx` → PDF in modalità headless).
- **WinCar** installato sullo stesso PC dove gira l'app (per accesso diretto ai `.mdb`).

## Installazione

```bash
# 1. Clone
git clone https://github.com/<your-username>/lys-workflow-hub.git
cd lys-workflow-hub

# 2. Virtualenv
python -m venv .venv
.venv\Scripts\activate

# 3. Dipendenze
pip install -r requirements.txt

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
├── README.md                       Questo file
├── .gitignore                      Esclude dati reali e credenziali
├── .env.example                    Template di configurazione (sicuro)
├── requirements.txt                Dipendenze Python
├── pyproject.toml                  Metadati del pacchetto
├── docs/                           Documenti di analisi e decisioni
│   ├── Analisi_App_Cessione_del_Credito.docx     (v1, storico)
│   ├── Analisi_LYS_Workflow_Hub_v2.docx           (v2, attuale)
│   └── Decisioni_finalizzate_v2.docx              (registro decisioni)
├── scripts/                        Utility one-shot (dump schema, ecc.)
│   └── dump_schema_wincar.py
├── src/
│   └── lys_workflow_hub/           Pacchetto principale
│       ├── __init__.py
│       ├── main.py                 Entry point FastAPI
│       ├── config.py               Caricamento .env, costanti
│       ├── core/
│       │   ├── wincar_repository.py    Lettura .mdb in sola lettura
│       │   └── schema_check.py         Verifica schema al boot
│       ├── workflows/
│       │   ├── cessione_credito/       Workflow A
│       │   └── risarcimento_vandalismo/ Workflow B (M2)
│       │       ├── data.py             modello RichiestaVandalismoData
│       │       ├── allegati.py         scanner cartelle Foto/ e Allegati/
│       │       └── pec_generator.py    builder oggetto + corpo PEC
│       ├── integrations/           Lettore posta, AI, notifiche
│       └── web/                    Route FastAPI + template Jinja2
│           ├── routes.py               pagine pratica + workflow cessione (M1)
│           ├── routes_vandalismo.py    pagine workflow vandalismo (M2)
│           └── routes_compagnie.py     CRUD anagrafica compagnie (M2)
├── tests/                          Test unitari
└── data/                           DB SQLite locale, eml_archive (gitignored)
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

**Prima di ogni `git push` verifica con `git status` che nessun file con dati reali
sia in stage.**

## Licenza

Codice proprietario sviluppato per Carrozzeria LYS Auto srl. Nessuna ridistribuzione
senza autorizzazione esplicita.

## Contatti progetto

- Project owner: Luca Pietroni — luca.pietroni@gmail.com
- Sviluppo: assistito da Claude (Anthropic)
