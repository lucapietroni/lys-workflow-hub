# Audit UI/UX — LYS Workflow Hub

Branch: `ui-ux-redesign`. Analisi di `base.html`, `style.css` (1868 righe), e
35 template. Nessuna modifica a logica/route/DB in questo audit.

| # | Problema | Gravità | Soluzione |
|---|----------|---------|-----------|
| 1 | UI browser vs app completamente diverse: tutto il redesign mobile (`html.is-app`, ~330 righe CSS) è invisibile da browser mobile — stessa persona che apre `hub.lysauto.it` da telefono vede una UX peggiore di chi apre l'app. | **Alta** | Spostare le regole di *layout* da `html.is-app` a media query per viewport. `is-app` resta solo per feature native (elencate dall'utente). |
| 2 | Bug link attivo: su `/portale/calendario` e `/portale/impostazioni` risultano **due** voci di nav attive insieme (`path.startsWith(href)` matcha sia `/portale` che `/portale/calendario`). | **Media** | Riscrivere lo script: tra tutti gli href che matchano, tenere solo il più lungo (match esatto preferito). |
| 3 | Hamburger `<input type="checkbox">` invece di un bottone reale: nessun `aria-expanded`, non richiudibile con ESC/tap-esterno, niente scroll-lock del body. | **Media** | Bottone reale con `aria-expanded`/`aria-controls`, JS per ESC/click-esterno/click-link, `overflow:hidden` su `<body>` quando aperto. |
| 4 | Nav admin: 12 voci piatte in fila, su tablet/mobile scrolla orizzontalmente dentro l'header (`overflow-x:auto` sulla nav) — utilizzabile ma poco scopribile. | **Media** | Raggruppare in 4 gruppi (Operatività/Comunicazioni/Amministrazione/Sistema) con `<details>` — dropdown su desktop, accordion verticale su mobile, stesso markup. |
| 5 | Home: hero con eyebrow+h1+sottotitolo (~110px) prima di vedere una sola pratica. | **Bassa/Media** | Comprimere hero, sottotitolo una riga, KPI e ricerca subito visibili. |
| 6 | Tabella pratiche: badge stato va a capo su due righe quando la colonna è stretta (già mitigato in v4.16.1 con `white-space:nowrap`, ma la trasformazione a **card** su mobile esiste solo `html.is-app` — da browser resta tabella con scroll orizzontale). | **Alta** | Generalizzare la trasformazione tabella→card (già scritta, vedi #1) a tutti i viewport stretti. |
| 7 | Appuntamenti: la card "data grande/titolo/pratica" esiste solo in app; da browser è la vecchia riga piatta con badge+testo unico. | **Media** | Stessa cosa di #1: card ovunque sotto una certa larghezza. |
| 8 | Dettaglio pratica: le azioni (Foto/Nota/Evento) sono sezioni sparse lungo la pagina, nessuna scorciatoia. Su mobile serve scrollare parecchio. | **Media** | Action bar fissa in fondo allo schermo (mobile) con Foto/Nota/Evento/Altro → ancore alle sezioni. |
| 9 | Contrasto: `--lys-grey-500` (testo secondario/meta) è **4.13:1** contro lo sfondo delle card — sotto la soglia AA (4.5:1) per testo normale. Verificato con calcolo WCAG relativo, non solo visivo. | **Alta** (accessibilità) | Schiarire il token a ~`#7E96B8` (5.5:1), zero altre modifiche cromatiche necessarie — badge/testo primario già ≥6.6:1. |
| 10 | Touch target: `.btn-sm` (4px 10px, ~28px altezza) sotto il minimo 44px consigliato; alcuni bottoni azione nelle liste (Apri →, Segna come vista) sono piccoli su mobile. | **Media** | `min-height:44px` sui controlli interattivi principali sotto una soglia di viewport, mantenendo `.btn-sm` compatto solo dove non è l'azione primaria di riga. |
| 11 | Google Fonts (`@import` da `fonts.googleapis.com`) blocca il render finché non risponde un host esterno — niente self-hosting, niente `font-display` esplicito nell'`@import`. | **Media** (performance) | Passare a stack di font di sistema (`-apple-system, "Segoe UI", Roboto, ...`) — zero richieste di rete, look quasi identico, niente FOUC. |
| 12 | `prefers-reduced-motion` assente: tutte le `animation: fade-up` (card, hero, risultati) girano sempre, anche per chi ha disattivato le animazioni a livello OS. | **Bassa** | `@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }`. |
| 13 | Glass/blur pervasivo (backdrop-filter fino a 20px su header, 8-12px su ogni card) — costoso su device di fascia bassa (compositing GPU ripetuto), e riduce leggermente il contrasto dei bordi. | **Bassa/Media** (performance + leggibilità) | Ridurre blur (20px→10px header, 8-12px→4-6px altrove), ridurre ombre superflue. |
| 14 | Footer identico per admin ed esterno (versione + "WinCar in sola lettura" + sviluppatore) — un'agenzia esterna non ha bisogno di sapere la versione interna del gestionale. | **Bassa** | Footer conciso per esterno (solo ragione sociale), footer tecnico invariato per admin. |
| 15 | 262 `style="..."` inline nei template (spaziature una-tantum, per lo più innocue ma non riusabili) — non centrale per l'obiettivo, footprint enorme da azzerare del tutto in un solo giro. | **Bassa** | Estratti in classi solo dove il pattern si ripete (form-actions inline, badge inline); il resto lasciato — rischio/beneficio non giustifica una riscrittura totale di 35 template. |
| 16 | Font-size/spacing "storici" (accumulati per feature successive) senza una scala dichiarata — es. badge a 0.73/0.78rem, meta a 0.85/0.88/0.91rem senza motivo evidente. | **Bassa** | Introdotta una scala dichiarata (`--space-*`, `--fs-*`) per i componenti nuovi/toccati in questo giro; non retrofit totale delle 1868 righe esistenti (fuori scope proporzionato). |

## Cosa NON viene toccato
Logica applicativa, route, permessi, DB, autenticazione — solo `base.html`,
`style.css`, e i template di home/lista pratiche/dettaglio
pratica/appuntamenti/portale esplicitamente citati dalla richiesta. Le
sotto-pagine admin (compagnie/utenti/bozze/risposte/PEC/statistiche)
ricevono i miglioramenti di sistema (contrasto, font, badge, form, focus,
motion) ma non un redesign card-per-riga dedicato — restano tabelle con
scroll orizzontale protetto (comportamento già presente, non regressivo).

Procedo con l'implementazione.
