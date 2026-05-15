# M3 — Decisioni di progetto (Workflow C: Lettura risposte assicurazioni)

Data: 15 maggio 2026

## 1. Architettura: job schedulato vs. servizio sempre attivo

Il polling delle PEC in entrata gira come **script standalone schedulato**
(`scripts/run_polling.py`), non come thread interno all'app web. Vantaggi:

- L'app web FastAPI continua a fare solo il suo lavoro (mostrare dati,
  inviare PEC). Niente accoppiamento con un job background.
- Il task scheduler Windows è già usato per altro (avvio app), aggiungere
  un secondo task per il polling è banale.
- Eseguire 2 volte al giorno (09:00 e 17:00) come da scelta operativa
  significa zero dipendenze runtime da uvicorn / FastAPI.
- Tracker dei costi AI gestito a livello DB (singola fonte di verità).

Trade-off: la reattività è alla mezza giornata. Non c'è notifica entro
i 5 minuti dalla ricezione. È coerente con la natura del problema:
le risposte delle compagnie non sono mai urgenti minuto-per-minuto.

## 2. Matching ibrido header + euristica

Le compagnie usano gestionali diversi; alcuni mantengono `In-Reply-To`,
altri rigenerano il thread. Doppia strategia (in ordine):

1. **Header RFC 2822** (`In-Reply-To`, poi `References`): match diretto con
   `pec_inviate.message_id`. Confidence 1.0 (o 0.95 per References).
2. **Euristica regex** su oggetto + body: cerca targa italiana, numero
   pratica WinCar, numero polizza. Per ogni PEC inviata calcola uno score
   basato su quanti segnali combaciano con l'oggetto della PEC inviata.
   Soglia di accettazione: 0.6. Cap massimo: 0.9 (l'euristica non è mai
   certa al 100%).
3. **Nessun match**: la mail viene archiviata + classificata, ma senza
   pratica collegata. L'operatore può poi gestirla a mano.

## 3. Tassonomia AI: 5 categorie + tutte 4 actionable

Le 5 categorie scelte coprono il ciclo di vita standard di una pratica
sinistri:

- **presa_in_carico**: la compagnia apre il sinistro.
- **nomina_perito**: incarica un perito (la più "actionable" per noi).
- **richiesta_documenti**: vuole documenti integrativi.
- **liquidazione**: comunica importo / pagamento.
- **altro**: tutto il resto (newsletter, ricevute PEC, comunicazioni generiche).

Le prime 4 sono potenzialmente `action_required=True`; "altro" è sempre
informativo. È l'AI a decidere `action_required` per ogni caso specifico:
una "presa in carico" senza richieste può non richiedere azioni; una con
"si prega di confermare entro 5gg" sì.

## 4. Modello AI: Claude Haiku 4.5 di default

Scelto per il rapporto costo/qualità:

- Input ~1 USD / 1M token, Output ~5 USD / 1M token (maggio 2026).
- Una mail tipica = ~500–1500 token input + ~150 token output = ~0.001 EUR.
- Con budget mensile 20 EUR coprire migliaia di mail.

Per i casi limite (linguaggio gergale, mail composte male) si può
passare a Sonnet 4.6 cambiando `ANTHROPIC_MODEL` in `.env`. Il costo
sale a ~0.005 EUR per mail, comunque sostenibile.

Modalità `AI_DISABLED=true` salta del tutto le chiamate API e ritorna
categoria "altro" con confidence 0. Utile per il rodaggio e per i test.

## 5. Output strutturato: JSON robusto

Il classificatore istruisce Claude a rispondere **solo** con un singolo
oggetto JSON che rispetta uno schema predefinito (categoria, confidence,
summary, action_required, key_facts). Tre livelli di robustezza:

1. Prompt esplicito "rispondi SOLO con JSON, niente markdown".
2. Parser tollerante (`_safe_parse_json`): estrae il primo blocco JSON
   valido anche se circondato da fence ```` ```json ```` o testo extra.
3. Fallback: se il parser fallisce, categoria="altro" con summary che
   cita la prima riga del body. La mail è comunque archiviata, manca
   solo la classificazione strutturata.

## 6. Notifiche: due canali, indipendenti

- **Push istantaneo (ntfy.sh)**: una notifica per ogni risposta classificata
  con `action_required=True`. Title = categoria + pratica. Body = summary +
  key_facts. Click sulla notifica → apre l'app sulla pagina del dettaglio.
  Richiede `NTFY_TOPIC` segreto.
- **Email riassuntiva (SMTP non-PEC)**: a fine ciclo polling, una email
  ad `ALERT_EMAIL` con riepilogo di tutte le nuove risposte raggruppate
  per categoria. Inviata via `mail.tophost.it:587 STARTTLS`.

Entrambi disattivabili separatamente. `NOTIFY_DISABLED=true` blocca
tutto (modalità silenziosa).

## 7. Storage delle mail

Tre layer:

- **Filesystem**: file `.eml` grezzi in `C:\LYSApp\Mail_in\<anno>\<casella>\`
  con nome `<timestamp>_<subject-slug>.eml`. Backup completo del contenuto.
- **DB tabella `mail_in`**: una riga per messaggio con header essenziali +
  estratto del body (~8000 char). Indice unico su `(casella, message_id)`
  e `(casella, uid_imap)` per dedup automatico.
- **DB tabella `mail_classificate`**: classificazione AI + matching con PEC
  inviata e pratica WinCar. 1:1 con `mail_in` (max una classificazione per
  mail; rieseguire una classificazione richiede di cancellare il record).

## 8. Fetch incrementale per UID

L'IMAP fetcher chiede solo gli UID > `MAX(uid_imap)` già presente in DB
per la casella corrente. Niente ridownload di tutto. Funziona perché:

- gli UID sono monotonicamente crescenti finché non c'è UIDVALIDITY change;
- in caso di UIDVALIDITY change (raro: cambio server, ripristino backup),
  il fetcher ripartirà da 0 — duplicati verranno comunque deduplicati per
  Message-ID nella tabella `mail_in`.

## 9. Sicurezza credenziali

Tutte le credenziali (PEC IMAP, email IMAP, Anthropic API key, SMTP)
vivono in `.env`, che è in `.gitignore` e non lascia mai il PC carrozzeria.
La password PEC InfoCert va impostata a mano la prima volta dalla pagina
admin di Legalmail (sì, scocciatura, ma è l'unico modo per garantire che
non sia in chiaro nei backup).

## 10. Test

Nuovi test in `tests/test_m3_pipeline.py` (~20 test):

- Estrattore segnali regex (targa, pratica, polizza).
- Matcher: header In-Reply-To, header References, euristica, nessun match.
- AI Classifier: modalità disabled, no API key, mock Anthropic (parsing
  della risposta JSON, categoria invalida → altro, JSON dentro markdown).
- Notifier: modalità disabled, push solo per action_required, email
  riassuntiva raggruppata per categoria, formato push con key_facts.
- Cost calculator: verifica del calcolo USD→EUR con tariffario Haiku.

Tutti i test girano su Linux/macOS senza WinCar/Access (mock di Anthropic
via `unittest.mock.patch`).
