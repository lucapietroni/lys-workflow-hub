"""Generatore AI del corpo di una bozza di risposta (M4).

Wrapper sopra Anthropic Claude API che, dato un contesto (classificazione
M3 + dati pratica + testo originale anonimizzato), restituisce il corpo
da incollare nello scaffold.

Architettura allineata a `integrations/ai_classifier.py`:

  * pricing tabellato per modello, cost in EUR convertito da USD;
  * modalita' `disabled=True` (env `AI_DISABLED=true`) o `api_key` vuota
    -> fallback testuale safe, costo zero;
  * eccezioni dell'SDK gestite -> fallback con messaggio esplicativo;
  * niente parser JSON: qui ci aspettiamo testo libero (il modello
    risponde con il body del messaggio, non con uno schema strutturato).

Output ben tipizzato (`BodyGenerationResult`) che il `draft_service` puo'
salvare direttamente nel campo `ai_model` / `ai_cost_eur` della Draft.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from lys_workflow_hub.workflows.risposte.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
)


logger = logging.getLogger(__name__)


# Pricing USD per 1M token (allineato a ai_classifier.py).
_PRICING_USD = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}
_USD_TO_EUR = 0.92


@dataclass(frozen=True)
class BodyGenerationResult:
    """Esito della chiamata AI per generare il corpo bozza."""

    body: str
    ai_model: str
    ai_cost_eur: float
    fallback: bool = False
    fallback_reason: str = ""


# --------------------------------------------------------------------------- #
#  Cost
# --------------------------------------------------------------------------- #


def _compute_cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING_USD.get(model) or _PRICING_USD["claude-haiku-4-5"]
    usd = (input_tokens / 1_000_000) * pricing["input"] + (
        output_tokens / 1_000_000
    ) * pricing["output"]
    return round(usd * _USD_TO_EUR, 6)


# --------------------------------------------------------------------------- #
#  Fallback
# --------------------------------------------------------------------------- #


def _fallback_body(*, summary_m3: str, reason: str) -> BodyGenerationResult:
    """Body testuale safe quando l'AI non e' disponibile o ha errori.

    Non e' una "bozza inviabile": e' un placeholder che invita l'operatore
    a scrivere il corpo a mano. Lo scaffold continua a fornire intestazione
    e firma, quindi il messaggio rimane formalmente coerente.
    """
    summary = (summary_m3 or "").strip() or "(nessun riassunto disponibile)"
    body = (
        "[BOZZA NON GENERATA AUTOMATICAMENTE: redigere il corpo a mano.]\n\n"
        f"Riassunto del messaggio ricevuto: {summary}\n\n"
        "Si conferma la ricezione della Vs. comunicazione. Seguira' "
        "riscontro dettagliato a breve."
    )
    return BodyGenerationResult(
        body=body,
        ai_model="(fallback)",
        ai_cost_eur=0.0,
        fallback=True,
        fallback_reason=reason,
    )


# --------------------------------------------------------------------------- #
#  API pubblica
# --------------------------------------------------------------------------- #


def genera_body(
    *,
    categoria: str,
    summary_m3: str,
    key_facts: dict,
    testo_originale_anon: str,
    pratica_numero: int | None,
    sinistro_numero: str,
    polizza_numero: str,
    veicolo_targa: str,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 800,
    disabled: bool = False,
) -> BodyGenerationResult:
    """Genera il corpo della bozza chiamando Claude API.

    Non solleva mai eccezioni: in caso di problema (AI disabilitata, SDK
    mancante, errore di rete, risposta vuota) ritorna un `BodyGenerationResult`
    in modalita' fallback.
    """
    if disabled or not api_key or not api_key.strip():
        return _fallback_body(summary_m3=summary_m3, reason="AI disattivata")

    try:
        import anthropic  # import locale: nessun costo se AI off
    except ImportError:
        return _fallback_body(
            summary_m3=summary_m3, reason="anthropic SDK non installato"
        )

    user_prompt = build_user_prompt(
        categoria=categoria,
        summary_m3=summary_m3,
        key_facts=key_facts or {},
        testo_originale_anon=testo_originale_anon,
        pratica_numero=pratica_numero,
        sinistro_numero=sinistro_numero,
        polizza_numero=polizza_numero,
        veicolo_targa=veicolo_targa,
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=int(max_tokens),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore Anthropic API in body_generator: %s", exc)
        return _fallback_body(summary_m3=summary_m3, reason=f"errore API: {exc}")

    # Estrazione del testo dalla risposta.
    text_chunks: list[str] = []
    try:
        for block in msg.content:
            txt = getattr(block, "text", "")
            if txt:
                text_chunks.append(txt)
    except (AttributeError, TypeError):
        pass
    body_raw = "".join(text_chunks).strip()

    if not body_raw:
        return _fallback_body(
            summary_m3=summary_m3, reason="risposta AI vuota"
        )

    # Cost
    usage = getattr(msg, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost_eur = _compute_cost_eur(model, input_tokens, output_tokens)

    return BodyGenerationResult(
        body=body_raw,
        ai_model=model,
        ai_cost_eur=cost_eur,
        fallback=False,
        fallback_reason="",
    )


__all__ = [
    "BodyGenerationResult",
    "genera_body",
]
