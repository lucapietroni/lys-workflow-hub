"""Policy che decide, per ogni categoria classificata da M3, se M4 deve
generare automaticamente una bozza, proporla on-demand o ignorare.

Esposta come modulo separato perche':

  * E' la regola di business che vorrai modificare nel tempo senza toccare
    `draft_service.py` (cuore della pipeline).
  * Permette di scrivere test mirati sul comportamento per categoria.

Comportamento per categoria
---------------------------

  BOZZA_AUTO
    Non appena M3 termina di classificare la mail, M4 prepara
    automaticamente una bozza in stato `pending` pronta per l'editor.

  BOZZA_OPT_IN
    Nessuna bozza creata automaticamente. L'operatore puo' richiederne la
    generazione esplicitamente dal cruscotto (azione "Genera bozza").

  BOZZA_NESSUNA
    Categoria informativa: rispondere non porta valore (es. presa in
    carico, ricevuta PEC di sistema). Il pulsante "Genera bozza" puo'
    rimanere comunque disponibile come opt-in manuale, ma la default
    suggerita all'UI e' "nessuna risposta necessaria".

Default proposti (modificabili senza ricompilare cambiando solo questo dict):

  * presa_in_carico    -> nessuna  (la compagnia ha aperto il sinistro, ok cosi')
  * nomina_perito      -> opt-in   (a volte serve confermare contatto, a volte no)
  * richiesta_documenti-> auto     (caso d'uso principale di M4)
  * liquidazione       -> auto     (conferma + eventuali coordinate per pagamento)
  * altro              -> opt-in   (per definizione caso-per-caso)
"""
from __future__ import annotations

from lys_workflow_hub.core.mail_in_repository import (
    CAT_ALTRO,
    CAT_LIQUIDAZIONE,
    CAT_NOMINA_PERITO,
    CAT_PRESA_IN_CARICO,
    CAT_RICHIESTA_DOCUMENTI,
    CATEGORIE,
)


# Esiti possibili della policy.
BOZZA_AUTO = "auto"
BOZZA_OPT_IN = "opt_in"
BOZZA_NESSUNA = "nessuna"

POLICIES = (BOZZA_AUTO, BOZZA_OPT_IN, BOZZA_NESSUNA)


# Mapping categoria -> policy. Modificabile dall'operatore in futuro via UI
# o file YAML; per ora e' una costante a codice.
DEFAULT_POLICY_PER_CATEGORIA: dict[str, str] = {
    CAT_PRESA_IN_CARICO: BOZZA_NESSUNA,
    CAT_NOMINA_PERITO: BOZZA_OPT_IN,
    CAT_RICHIESTA_DOCUMENTI: BOZZA_AUTO,
    CAT_LIQUIDAZIONE: BOZZA_AUTO,
    CAT_ALTRO: BOZZA_OPT_IN,
}


def policy_per(categoria: str) -> str:
    """Ritorna la policy M4 per una categoria classificata.

    Fallback conservativo: per categorie sconosciute (es. taxonomy aggiunta
    in futuro a M3 ma non ancora qui), ritorna `opt_in` — niente bozza
    automatica, ma neanche silenziamento.
    """
    return DEFAULT_POLICY_PER_CATEGORIA.get(categoria, BOZZA_OPT_IN)


def deve_generare_auto(categoria: str) -> bool:
    """True se M3, una volta classificata la categoria, deve invocare M4
    per la generazione automatica della bozza."""
    return policy_per(categoria) == BOZZA_AUTO


def consente_opt_in(categoria: str) -> bool:
    """True se l'operatore puo' chiedere a M4 di generare comunque una
    bozza dal cruscotto, anche se la policy automatica non lo prevede.
    Sempre True tranne casi futuri di hard-disable."""
    return policy_per(categoria) in (BOZZA_AUTO, BOZZA_OPT_IN, BOZZA_NESSUNA)


__all__ = [
    "BOZZA_AUTO",
    "BOZZA_OPT_IN",
    "BOZZA_NESSUNA",
    "POLICIES",
    "DEFAULT_POLICY_PER_CATEGORIA",
    "policy_per",
    "deve_generare_auto",
    "consente_opt_in",
    "CATEGORIE",  # re-export di cortesia
]
