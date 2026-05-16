"""Workflow C — Lettura risposte assicurazioni (M3) + bozze di risposta (M4).

Espone le funzioni principali del workflow per essere importate dai router web
e dallo script di polling.
"""
from lys_workflow_hub.workflows.risposte.attachments import (
    SuggestionResult,
    conta_inclusi,
    suggerisci,
)
from lys_workflow_hub.workflows.risposte.body_generator import (
    BodyGenerationResult,
    genera_body,
)
from lys_workflow_hub.workflows.risposte.categorie_policy import (
    BOZZA_AUTO,
    BOZZA_NESSUNA,
    BOZZA_OPT_IN,
    DEFAULT_POLICY_PER_CATEGORIA,
    consente_opt_in,
    deve_generare_auto,
    policy_per,
)
from lys_workflow_hub.workflows.risposte.draft_service import (
    EsitoSpedizione,
    GenerationResult,
    ParametriSpedizione,
    SendResult,
    aggiorna_bozza,
    annulla_bozza,
    crea_bozza_se_serve,
    genera_bozza,
    invia_bozza,
)
from lys_workflow_hub.workflows.risposte.matcher import (
    MatchResult,
    match_mail,
)
from lys_workflow_hub.workflows.risposte.scaffold import (
    ScaffoldContext,
    anonimizza_testo_originale,
    build_body,
    build_subject,
)
from lys_workflow_hub.workflows.risposte.sender import (
    spedisci,
)


__all__ = [
    # M3 — matcher
    "MatchResult",
    "match_mail",
    # M4 — policy categorie
    "BOZZA_AUTO",
    "BOZZA_OPT_IN",
    "BOZZA_NESSUNA",
    "DEFAULT_POLICY_PER_CATEGORIA",
    "policy_per",
    "deve_generare_auto",
    "consente_opt_in",
    # M4 — attachments
    "SuggestionResult",
    "suggerisci",
    "conta_inclusi",
    # M4 — scaffold
    "ScaffoldContext",
    "build_subject",
    "build_body",
    "anonimizza_testo_originale",
    # M4 — body generator
    "BodyGenerationResult",
    "genera_body",
    # M4 — sender
    "ParametriSpedizione",
    "EsitoSpedizione",
    "spedisci",
    # M4 — draft service (orchestrazione)
    "GenerationResult",
    "SendResult",
    "genera_bozza",
    "crea_bozza_se_serve",
    "aggiorna_bozza",
    "annulla_bozza",
    "invia_bozza",
]
