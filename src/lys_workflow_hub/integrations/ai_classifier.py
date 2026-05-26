"""Classificatore AI delle risposte assicurative (M3).

Usa l'API Anthropic Claude per classificare una mail in una delle 5 categorie
predefinite (presa_in_carico, nomina_perito, richiesta_documenti, liquidazione,
altro) ed estrarre key_facts utili (numero sinistro, importo, perito, scadenza).

Output strutturato: l'API viene istruita a rispondere SOLO con un JSON che
rispetti lo schema. Tre livelli di robustezza:

  1. Prompt che chiede esplicitamente JSON-only.
  2. Parser tollerante (estrazione del primo blocco JSON valido nel response).
  3. Fallback a categoria "altro" + confidence=0 se il parsing fallisce.

Cost tracking: per ogni chiamata, calcola il costo stimato basandosi sul
pricing pubblico del modello (input + output tokens). Il costo viene poi
sommato in `mail_classificate.ai_cost_eur` e usato per il budget mensile.

In modalità `ai_disabled=True` (env) la classificazione viene saltata e
restituisce categoria=altro con un summary che cita il primo riga del body.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from lys_workflow_hub.core.mail_in_repository import (
    CATEGORIE,
    CAT_ALTRO,
)


logger = logging.getLogger(__name__)


# Pricing approssimativo (USD per 1M token) — fonte: pricing pubblico Anthropic
# a maggio 2026. Da aggiornare se cambia. Convertito in EUR con cambio 1:0.92.
_PRICING_USD = {
    # claude-haiku-4-5
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    # claude-sonnet-4-6
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    # claude-opus-4-6
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}
_USD_TO_EUR = 0.92


# --------------------------------------------------------------------------- #
#  Modello di ritorno
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClassificationResult:
    categoria: str
    confidence: float
    summary: str
    action_required: bool
    key_facts: dict
    ai_model: str
    ai_cost_eur: float


# --------------------------------------------------------------------------- #
#  Prompt
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT = """Sei un assistente che classifica le risposte di compagnie \
assicurative italiane a richieste di risarcimento per atti vandalici inviate \
da una carrozzeria.

Devi classificare ogni email in ESATTAMENTE una di queste 5 categorie:

- "presa_in_carico": la compagnia conferma di aver ricevuto la richiesta e \
  apre il sinistro, assegnando di solito un numero pratica/sinistro.
- "nomina_perito": la compagnia incarica un perito di esaminare il veicolo. \
  Spesso indica il nome del perito o dell'agenzia peritale e chiede di \
  contattarli o di farsi contattare per fissare l'appuntamento.
- "richiesta_documenti": la compagnia chiede documenti aggiuntivi prima di \
  procedere (foto, denuncia integrativa, dichiarazione testimoni, certificati \
  vari, libretti, patente, ecc.).
- "liquidazione": la compagnia comunica l'importo che riconosce e liquida, \
  oppure conferma l'avvenuto pagamento. Usa questa categoria SOLO se c'è un \
  importo concreto o una conferma esplicita di pagamento.
- "altro": tutto ciò che non rientra nelle 4 categorie sopra. Comprende: \
  messaggi automatici, ricevute di consegna PEC, comunicazioni generiche, \
  E ANCHE dinieghi/rifiuti di copertura (es. "il veicolo non risulta \
  assicurato con noi", "polizza non trovata", "non siamo competenti per \
  questo sinistro"). I dinieghi NON sono liquidazioni.

Rispondi SEMPRE e SOLO con un singolo oggetto JSON che rispetta esattamente \
questo schema (niente testo prima o dopo, niente markdown):

{
  "categoria": "presa_in_carico" | "nomina_perito" | "richiesta_documenti" | \
"liquidazione" | "altro",
  "confidence": <numero tra 0 e 1, indica quanto sei sicuro>,
  "summary": "<frase italiana max 280 caratteri che riassume il messaggio per \
l'operatore della carrozzeria>",
  "action_required": <true se l'operatore deve fare qualcosa di esplicito \
(rispondere, mandare documenti, contattare il perito, ecc.); false se è una \
comunicazione informativa che non richiede azioni immediate>,
  "key_facts": {
    "numero_sinistro": "<stringa o null>",
    "importo_eur": <numero o null>,
    "perito": "<nome perito o agenzia peritale o null>",
    "scadenza": "<data scadenza in formato YYYY-MM-DD o null>"
  }
}

Se un key_fact non è presente nel messaggio, mettilo a null. Non inventare valori.
"""


def _build_user_prompt(*, subject: str, sender: str, body: str) -> str:
    return (
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"---\n"
        f"{body[:6000]}\n"
        f"---\n"
        "Classifica questa email rispettando lo schema JSON."
    )


# --------------------------------------------------------------------------- #
#  Cost
# --------------------------------------------------------------------------- #


def _compute_cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calcolo cost basato sul pricing tabella sopra."""
    pricing = _PRICING_USD.get(model)
    if not pricing:
        # fallback a haiku se modello sconosciuto
        pricing = _PRICING_USD["claude-haiku-4-5"]
    usd = (input_tokens / 1_000_000) * pricing["input"] + (
        output_tokens / 1_000_000
    ) * pricing["output"]
    return round(usd * _USD_TO_EUR, 6)


# --------------------------------------------------------------------------- #
#  Parser robusto del JSON di risposta
# --------------------------------------------------------------------------- #


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _safe_parse_json(raw: str) -> dict | None:
    """Tenta di estrarre il primo blocco JSON valido dalla stringa."""
    if not raw:
        return None
    candidate = raw.strip()
    # Rimuovi code-fences markdown se presenti.
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-z]*\n?", "", candidate, count=1)
        candidate = re.sub(r"\n?```$", "", candidate, count=1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Cerca il primo blocco {...} bilanciato (euristica semplice).
    m = _JSON_BLOCK_RE.search(candidate)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _normalize_result(data: dict, *, model: str, cost_eur: float) -> ClassificationResult:
    """Normalizza il dict ricevuto in un ClassificationResult, riempiendo
    eventuali campi mancanti con valori sicuri."""
    cat_raw = (data.get("categoria") or "").strip().lower()
    categoria = cat_raw if cat_raw in CATEGORIE else CAT_ALTRO
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    summary = (data.get("summary") or "").strip()[:300]
    action_required = bool(data.get("action_required"))
    key_facts = data.get("key_facts") or {}
    if not isinstance(key_facts, dict):
        key_facts = {}
    return ClassificationResult(
        categoria=categoria,
        confidence=confidence,
        summary=summary,
        action_required=action_required,
        key_facts=key_facts,
        ai_model=model,
        ai_cost_eur=cost_eur,
    )


def _fallback_result(*, body: str, model: str, reason: str) -> ClassificationResult:
    summary = (body or "").splitlines()[0] if body else ""
    summary = summary.strip()[:200] or "(nessun corpo)"
    return ClassificationResult(
        categoria=CAT_ALTRO,
        confidence=0.0,
        summary=f"[{reason}] {summary}",
        action_required=False,
        key_facts={},
        ai_model=model,
        ai_cost_eur=0.0,
    )


# --------------------------------------------------------------------------- #
#  API pubblica
# --------------------------------------------------------------------------- #


def classify(
    *,
    subject: str,
    sender: str,
    body: str,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    disabled: bool = False,
) -> ClassificationResult:
    """Classifica una email. Restituisce sempre un `ClassificationResult` (mai
    solleva eccezioni: in caso di errore, ritorna categoria=altro con
    summary che spiega il problema).

    Parametri:
      - `disabled`: skip totale dell'API, ritorna categoria=altro. Utile per
        test e per la modalità "AI off" via .env.
      - `api_key`: se vuoto, comportamento equivalente a `disabled=True`.
    """
    if disabled or not api_key or not api_key.strip():
        return _fallback_result(
            body=body,
            model=model,
            reason="AI disattivata",
        )

    try:
        import anthropic  # importato qui per evitare il costo se AI è off
    except ImportError:
        return _fallback_result(
            body=body,
            model=model,
            reason="anthropic SDK non installato",
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(subject=subject, sender=sender, body=body)

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore Anthropic API: %s", exc)
        return _fallback_result(
            body=body,
            model=model,
            reason=f"errore API: {exc}",
        )

    # Estrai testo della risposta (anthropic SDK ritorna lista di blocchi).
    text_chunks: list[str] = []
    try:
        for block in msg.content:
            txt = getattr(block, "text", "")
            if txt:
                text_chunks.append(txt)
    except (AttributeError, TypeError):
        pass
    raw = "".join(text_chunks).strip()

    parsed = _safe_parse_json(raw)
    if parsed is None:
        return _fallback_result(
            body=body,
            model=model,
            reason="risposta AI non parsabile",
        )

    # Cost dell'invocazione.
    usage = getattr(msg, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost_eur = _compute_cost_eur(model, input_tokens, output_tokens)

    return _normalize_result(parsed, model=model, cost_eur=cost_eur)
