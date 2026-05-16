"""Style guide e prompt per la generazione AI del corpo bozza (M4).

Strato intermedio del modello a tre strati. Per ogni categoria
classificata da M3 c'e' una style guide testuale che dice all'AI:

  * tono e registro;
  * lunghezza tipica;
  * cosa includere obbligatoriamente;
  * cosa NON fare (intestazione, firma, GDPR boilerplate, ecc.).

Output atteso dall'AI: SOLO il corpo del messaggio, da circa 3 a 8 frasi.
La cornice (intestazione "Spett.le", riferimenti, firma) viene poi
incollata dallo scaffold (`scaffold.py`).

Vincoli comuni a tutte le categorie:

  * italiano formale aziendale, terza persona plurale di cortesia
    (Vi, SS.LL.);
  * MAI scrivere intestazione tipo "Spett.le", saluti iniziali tipo
    "Spettabile Compagnia,", e MAI firma o data finale;
  * non aggiungere GDPR/disclaimer/footer legali;
  * non inventare informazioni: se un dato non e' nel contesto, non
    citarlo. In caso di incertezza, formulare in modo prudente.
"""
from __future__ import annotations

from lys_workflow_hub.core.mail_in_repository import (
    CAT_ALTRO,
    CAT_LIQUIDAZIONE,
    CAT_NOMINA_PERITO,
    CAT_PRESA_IN_CARICO,
    CAT_RICHIESTA_DOCUMENTI,
)


# --------------------------------------------------------------------------- #
#  System prompt (parte comune)
# --------------------------------------------------------------------------- #


SYSTEM_PROMPT = """Sei l'assistente di scrittura di Carrozzeria LYS Auto srl, \
una carrozzeria professionale che gestisce pratiche di sinistro per conto \
dei clienti.

Devi scrivere SOLO il corpo di una risposta formale a una compagnia \
assicurativa. La cornice (intestazione, riferimenti, firma) viene aggiunta \
automaticamente: tu produci esclusivamente il contenuto centrale del \
messaggio.

Regole assolute (sempre, indipendentemente dalla categoria):
- Italiano formale aziendale, terza persona plurale di cortesia (Vi, SS.LL.).
- Tono cordiale ma fermo, mai servile ne' colloquiale.
- NON scrivere intestazione (niente "Spett.le", "Egregi Signori", ecc.).
- NON scrivere saluti iniziali (niente "Spettabile Compagnia,").
- NON firmarti (niente "Distinti saluti", "Cordiali saluti", nomi, citta', date).
- NON aggiungere disclaimer GDPR ne' formule legali boilerplate.
- NON inventare dati che non sono nel contesto fornito.
- Se cita documenti o richieste della compagnia, usa le stesse parole della \
compagnia per evitare ambiguita'.

Lunghezza: tra 3 e 8 frasi, massimo 2 paragrafi. Mai liste numerate o \
elenchi puntati a meno che la categoria lo richieda esplicitamente."""


# --------------------------------------------------------------------------- #
#  Style guide per categoria
# --------------------------------------------------------------------------- #


_STYLE_RICHIESTA_DOCUMENTI = """Categoria: RICHIESTA DI INTEGRAZIONE DOCUMENTI.

La compagnia ha chiesto documenti aggiuntivi prima di procedere. Il \
corpo deve:
1. Confermare di aver ricevuto la richiesta.
2. Confermare la disponibilita' a integrare la documentazione.
3. Elencare i documenti che si trasmettono in allegato. Usa le stesse \
parole con cui la compagnia li ha richiesti, per evitare ambiguita'.
4. Dichiarare la disponibilita' per ulteriori chiarimenti.

Tono: collaborativo, professionale. Niente toni di scusa: la richiesta \
e' parte normale dell'iter."""


_STYLE_LIQUIDAZIONE = """Categoria: COMUNICAZIONE DI LIQUIDAZIONE.

La compagnia ha comunicato un importo riconosciuto o avvenuto pagamento. \
Il corpo deve:
1. Confermare di aver ricevuto la comunicazione.
2. Se la compagnia ha indicato un importo, citarlo testualmente (es. \
"l'importo di euro X,YZ").
3. Se la liquidazione e' a favore della Cessionaria (Carrozzeria LYS Auto \
srl), confermarlo e indicare di restare in attesa del pagamento secondo \
le coordinate gia' comunicate (NON inventare IBAN ne' coordinate \
bancarie: il dato e' nei documenti gia' inviati).
4. Se l'importo riconosciuto richiede chiarimenti (es. franchigia non \
prevista, voci escluse), formulare con prudenza una richiesta di \
specifica delle voci che compongono il calcolo.

Tono: pacato e formale. Non esprimere accettazione o rifiuto definitivo: \
la decisione finale resta umana."""


_STYLE_NOMINA_PERITO = """Categoria: NOMINA DEL PERITO.

La compagnia ha incaricato un perito di esaminare il veicolo. Il corpo \
deve:
1. Confermare di aver ricevuto la nomina.
2. Se il perito o l'agenzia peritale sono citati nel testo originale, \
ripeterli per chiarezza.
3. Dichiarare la disponibilita' della carrozzeria a fissare l'appuntamento \
per l'ispezione del veicolo, indicando una fascia oraria generica di \
disponibilita' (es. "dal lunedi' al venerdi', in orario di apertura") \
SENZA inventare giorni o orari specifici.
4. Indicare il contatto telefonico della carrozzeria come riferimento per \
concordare l'appuntamento (il telefono e' nei riferimenti del messaggio).

Tono: collaborativo, pratico. Niente attese di settimane: la \
disponibilita' e' immediata."""


_STYLE_ALTRO = """Categoria: ALTRO / NON CLASSIFICATA.

Il messaggio non rientra in una categoria standard. Il corpo deve:
1. Confermare di aver ricevuto la comunicazione.
2. Riassumere brevemente cosa si e' compreso del messaggio originale.
3. Chiedere conferma o chiarimento dei punti che richiedono azione da \
parte della carrozzeria.

Tono: cauto e neutro. Non promettere azioni specifiche fino a quando il \
contenuto non e' chiarito."""


# Mapping categoria -> style guide testuale.
STYLE_GUIDES: dict[str, str] = {
    CAT_RICHIESTA_DOCUMENTI: _STYLE_RICHIESTA_DOCUMENTI,
    CAT_LIQUIDAZIONE: _STYLE_LIQUIDAZIONE,
    CAT_NOMINA_PERITO: _STYLE_NOMINA_PERITO,
    CAT_ALTRO: _STYLE_ALTRO,
    # Per CAT_PRESA_IN_CARICO non c'e' uno style: la policy e' NESSUNA
    # quindi M4 non chiama l'AI in automatico. Se l'operatore forza
    # l'opt-in, usiamo lo style "ALTRO" (vedi `style_per_categoria`).
}


def style_per_categoria(categoria: str) -> str:
    """Style guide per una categoria. Fallback prudente ad 'ALTRO'."""
    return STYLE_GUIDES.get(categoria, _STYLE_ALTRO)


# --------------------------------------------------------------------------- #
#  Costruzione del user prompt
# --------------------------------------------------------------------------- #


def build_user_prompt(
    *,
    categoria: str,
    summary_m3: str,
    key_facts: dict,
    testo_originale_anon: str,
    pratica_numero: int | None,
    sinistro_numero: str,
    polizza_numero: str,
    veicolo_targa: str,
) -> str:
    """Compone il messaggio user per Claude API.

    Il prompt include:
      * la style guide della categoria,
      * il riassunto e i key_facts gia' estratti da M3 (gratuiti: niente
        ri-parsing del messaggio),
      * il testo originale (anonimizzato dallo scaffold),
      * i riferimenti pratica/sinistro/polizza/targa.

    Risparmia token rispetto a passare la mail intera: M3 ha gia' fatto
    estrazione, riusiamo il suo output.
    """
    style = style_per_categoria(categoria)

    rif_lines = []
    if pratica_numero is not None:
        rif_lines.append(f"- Numero pratica: {pratica_numero}")
    if sinistro_numero:
        rif_lines.append(f"- Numero sinistro: {sinistro_numero}")
    if polizza_numero:
        rif_lines.append(f"- Numero polizza: {polizza_numero}")
    if veicolo_targa:
        rif_lines.append(f"- Targa veicolo: {veicolo_targa}")
    riferimenti = "\n".join(rif_lines) if rif_lines else "(nessuno noto)"

    facts_lines = []
    for k, v in (key_facts or {}).items():
        if v is None or v == "":
            continue
        facts_lines.append(f"- {k}: {v}")
    facts = "\n".join(facts_lines) if facts_lines else "(nessuno)"

    testo = (testo_originale_anon or "").strip()
    if not testo:
        testo = "(testo originale non disponibile)"
    # Limitiamo per non sforare token: 4000 char sono ~1000 token, abbastanza
    # per il 99% delle PEC tipiche.
    testo = testo[:4000]

    summary = (summary_m3 or "").strip() or "(nessun riassunto)"

    return (
        f"{style}\n\n"
        f"RIFERIMENTI PRATICA:\n{riferimenti}\n\n"
        f"RIASSUNTO ESTRATTO DA M3:\n{summary}\n\n"
        f"DATI CHIAVE ESTRATTI DA M3:\n{facts}\n\n"
        f"TESTO ORIGINALE DELLA COMPAGNIA (ANONIMIZZATO):\n"
        f"---\n{testo}\n---\n\n"
        "Scrivi ora il corpo del messaggio rispettando le regole assolute "
        "del system prompt e la style guide di questa categoria. SOLO il "
        "corpo. Niente intestazione, niente saluti iniziali, niente firma."
    )


__all__ = [
    "SYSTEM_PROMPT",
    "STYLE_GUIDES",
    "style_per_categoria",
    "build_user_prompt",
]
