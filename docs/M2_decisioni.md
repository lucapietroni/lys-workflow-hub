# M2 — Decisioni di progetto (Workflow B: Richiesta risarcimento vandalismo)

Data: 13 maggio 2026

Questo file integra `Decisioni_finalizzate_v2.docx` con le scelte operative
prese durante lo sviluppo della milestone M2. Le decisioni sono state
discusse e validate con il proprietario di progetto via chat.

## 1. Formato della PEC: corpo email lungo + allegati

La PEC inviata alla compagnia è una email standard con corpo testuale
strutturato e completo, senza PDF formale aggiuntivo. La scelta è motivata
dal fatto che la compagnia comunque scansiona il contenuto della PEC come
testo strutturato e la verbosità in un PDF separato non aggiunge valore
operativo.

Il corpo segue la sequenza:

1. Spett.le compagnia + ufficio sinistri + indirizzo + PEC
2. Oggetto (replicato anche nell'header SMTP)
3. Apertura formale (riferimento alla garanzia "Atti vandalici")
4. DATI ASSICURATO (nominativo, CF, nascita, residenza, contatti, ditta opz.)
5. DATI POLIZZA (compagnia, numero, agenzia)
6. DATI VEICOLO (marca/modello, targa, telaio)
7. DATI EVENTO (data, ora, luogo, descrizione danni)
8. DENUNCIA ALLE AUTORITÀ (autorità, comando, data, protocollo)
9. CESSIONE DEL CREDITO (riferimento legale + ragione sociale carrozzeria)
10. DOCUMENTI ALLEGATI (elenco numerato dei file effettivamente allegati)
11. RICHIESTA (nomina perito + appuntamento + liquidazione al cessionario)
12. RIFERIMENTI PER LE COMUNICAZIONI (carrozzeria, contatti)
13. Chiusura + data + firma

I placeholder per i campi vuoti sono `________` (otto underscore), molto
visibili in caso di campo non completato per dimenticanza.

## 2. Storage allegati: due cartelle WinCar

WinCar separa già fisicamente foto e documenti:

- `Pratiche\<n>\Pubblici\Foto\` — immagini del veicolo / del danno
- `Pratiche\<n>\Pubblici\Allegati\` — documenti (denuncia, cessione, libretto, ecc.)

Lo scanner `workflows/risarcimento_vandalismo/allegati.py` legge entrambe
le cartelle e classifica i file per nome ed estensione in quattro categorie:
foto, denuncia, cessione, altro. La cartella `Allegati/` è la stessa già
usata da M1 per archiviare la cessione firmata: nessuna modifica al
comportamento M1.

## 3. Anagrafica PEC compagnie: SQLite locale + CRUD UI

Le PEC delle compagnie non vengono inserite ogni volta a mano nella
richiesta: l'app gestisce una propria anagrafica interna persistente.

- **Storage**: SQLite (`data/lys_hub.db`), tabella `compagnie_assicurative`.
  Nessun ORM (sqlite3 stdlib), schema gestito con `CREATE TABLE IF NOT EXISTS`.
- **Campi**: id, nome, pec, email, indirizzo, cap, città, provincia,
  ufficio_sinistri, note, nome_norm (chiave di lookup), created_at, updated_at.
- **CRUD**: pagine sotto `/compagnie` (lista, nuova, modifica, elimina).
- **Lookup automatico**: quando si apre la schermata vandalismo di una
  pratica, il nome `F_DEASCL` letto da WinCar viene confrontato con
  `nome_norm` (normalizzazione: lowercase + trim + rimozione punteggiatura
  e suffissi societari S.p.A./srl/sas/snc/assicurazioni/...). Se c'è
  match, PEC e indirizzo vengono precompilati. Se non c'è match, l'app
  mostra un alert con link diretto alla creazione del record.
- **Vincoli**: PEC è obbligatoria; coppia (PEC) ha indice unico parziale
  (sulle righe con `pec <> ''`) per evitare duplicati di indirizzo PEC.

## 4. Preparazione bozza ≠ invio effettivo

In questa fase (M2 core) l'app si limita a **preparare** la PEC:

- mostra oggetto + corpo pronti da copiare;
- elenca i percorsi assoluti degli allegati selezionati;
- offre il download della bozza come file `.txt` con header (destinatario,
  oggetto, lista percorsi allegati) da usare come check-list.

L'operatore poi incolla manualmente il corpo nel client PEC (Aruba/InfoCert
ecc.) e aggancia i file dai percorsi indicati. L'invio automatico via SMTP
è demandato a una sotto-fase M2bis successiva: questo permette di rivedere
ogni messaggio prima di inviarlo davvero — comportamento prudente nella
fase di rodaggio.

## 5. Schermata vandalismo: editabile + rigenerazione lato server

Coerentemente con M1 (cessione del credito), la schermata di anteprima è
un grosso form HTML editabile. Ogni modifica è applicata via POST allo
stesso URL (`/pratiche/<n>/vandalismo`), il server rigenera la bozza con
i nuovi valori e ri-renderizza la pagina. Non c'è stato lato client se
non per il pulsante "Copia corpo PEC".

Vantaggi: ogni rigenerazione passa per le stesse funzioni testabili lato
Python (`from_pratica` + `build_body`), zero divergenza tra anteprima e
output finale.

## 6. Settings nuove in `.env`

```
APP_DB_PATH=data/lys_hub.db
CARROZZERIA_PEC=
CARROZZERIA_EMAIL=
CARROZZERIA_TELEFONO=
CARROZZERIA_REFERENTE=
```

I quattro valori `CARROZZERIA_*` sono usati nel blocco
"RIFERIMENTI PER LE COMUNICAZIONI" del corpo PEC. Se vuoti, le righe
relative vengono semplicemente omesse.

## 7. Test

Tre nuovi file di test (~30 test totali, ~95% coverage dei moduli M2):

- `tests/test_compagnie_repository.py` — CRUD, normalizzazione nomi,
  lookup, vincoli di integrità.
- `tests/test_vandalismo_data.py` — `from_pratica`, override, decoding
  CF, validazione campi mancanti.
- `tests/test_vandalismo_allegati_e_pec.py` — scanner cartelle, generatore
  oggetto/corpo, presenza placeholder, elenco allegati nel body.

Tutti i test girano su Linux/macOS (non richiedono WinCar/Access).

---

## M2-bis — Decisioni di progetto (Invio reale via SMTP)

Data: 14 maggio 2026

### 8. Provider PEC: InfoCert/Legalmail

Server SMTP di default: `sendm.cert.legalmail.it` porta `465` (SSL implicito).
Per altri provider basta cambiare `PEC_SMTP_HOST/PORT` in `.env`. Il modulo
`integrations/pec_mailer.py` supporta automaticamente entrambi i flussi:

- porta 465 → `SMTP_SSL` (SSL implicito, tipico delle PEC)
- altre porte → `SMTP` + `STARTTLS` (es. 587)

### 9. Conferma pre-invio: pagina dedicata

Prima dell'invio reale, una pagina dedicata (`/pratiche/<n>/vandalismo/conferma`)
mostra:

- destinatario PEC, oggetto, corpo completo (read-only)
- elenco numerato degli allegati con dimensione totale del messaggio
- warning visibili se è attivo `PEC_DRY_RUN` o se mancano campi obbligatori
- bottone "Conferma e invia" con `confirm()` JavaScript come ulteriore conferma

Il bottone è disabilitato se mancano campi obbligatori o se manca la PEC
destinatario o mittente.

### 10. Dry-run via .env

`PEC_DRY_RUN=true` attiva la modalità dry-run: l'app costruisce normalmente
il messaggio `EmailMessage` (subject + body + allegati base64), lo archivia
su filesystem, e registra l'invio nel DB con esito `DRY_RUN`. **Non** apre
nessuna connessione SMTP. Permette di testare l'intero flusso (compresi
build MIME + dimensioni allegati + naming file `.eml`) senza disturbare
le compagnie.

### 11. Archiviazione: solo DB + archivio centrale, niente WinCar

Il file `.eml` viene scritto in `C:\LYSApp\PEC_inviate\<anno>\` con nome
deterministico `<YYYYMMDD-HHMMSS>_<numpra>_<compagnia>.eml`. Il record DB
`pec_inviate` punta al path assoluto. **Non viene** copiato in
`Pratiche/<n>/Pubblici/Allegati/` per non duplicare contenuto: l'audit
trail strutturato (DB + filesystem) è già sufficiente.

### 12. Ordinazione delle operazioni: archivia prima di inviare

L'orchestratore `workflows/risarcimento_vandalismo/invio_pec.py` segue
questa sequenza:

1. **build_message** — costruisce l'EmailMessage RFC-822 (in memoria, niente rete).
2. **archivia .eml su filesystem** — write atomico prima dell'invio: se il
   network esplode, il messaggio non si perde.
3. **send_message** — invio SMTP (o dry-run).
4. **log nel DB** — record `pec_inviate` con esito (OK / DRY_RUN / KO).

Anche in caso di errore SMTP, il record DB e il file `.eml` restano in
archivio: l'audit trail copre sia successi sia fallimenti.

### 13. Limiti dimensione

`build_message` valida due soglie consigliate per non incorrere in
rifiuti dei provider PEC:

- `PEC_MAX_ATTACHMENT_BYTES = 25 MB` per singolo file
- `PEC_MAX_TOTAL_BYTES = 30 MB` per messaggio complessivo

Se l'operatore tenta di costruire un messaggio oltre soglia, la build
fallisce con `ValueError` esplicito e l'invio non parte.
