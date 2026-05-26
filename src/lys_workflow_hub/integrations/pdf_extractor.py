"""Estrazione testo da allegati PDF nelle risposte assicurative (M5.3).

Molte compagnie assicurative inviano la risposta reale (presa in carico,
nomina perito, liquidazione) come PDF allegato invece di scriverla nel corpo
dell'email. Il testo del body rimane un pro-forma generico tipo "Si veda
l'allegato" o addirittura vuoto.

Questo modulo estrae il testo dai PDF allegati per darlo in pasto al
classificatore AI (M3), allargando enormemente la copertura di classificazione.

Dipendenza: `pypdf>=4.0` (puro Python, niente Ghostscript). Installato in
`requirements.txt`. Se per qualsiasi motivo non è disponibile, le funzioni
degradano silenziosamente (ritornano stringa vuota) senza bloccare il polling.

Nota: `pypdf` estrae solo il testo selezionabile (layer testo del PDF).
PDF che sono scansioni di carta (immagini embedded senza testo selezionabile)
producono stringa vuota — per quelli serve OCR, non implementato qui.
"""
from __future__ import annotations

import io
import logging
import re
from email.message import EmailMessage


logger = logging.getLogger(__name__)

# Numero massimo di caratteri estratti da un singolo PDF.
_PDF_CHARS_LIMIT = 4000

# Numero massimo di PDF allegati da processare per mail (per sicurezza).
_MAX_PDF_ATTACHMENTS = 3

# Parole chiave nel body che indicano "il contenuto reale è nell'allegato":
# bypassa il controllo min_body_len anche se il body è lungo per via del
# testo quotato del messaggio originale in risposta.
# Pattern per individuare l'inizio del testo quotato in un reply email.
# Quando il mittente ha scritto "in allegato" + incollato la sua risposta, il
# corpo include l'originale quotato. Troncare prima del testo quotato evita di
# dare all'AI il testo della PEC CHE ABBIAMO INVIATO NOI (confonde la categoria).
_QUOTE_START_PATTERNS = [
    re.compile(r"(?m)^>", re.MULTILINE),                     # >quoted line (con o senza spazio)
    re.compile(r"(?m)^-{5,}", re.MULTILINE),                 # -----
    re.compile(r"(?m)^_{5,}", re.MULTILINE),                 # _____
    re.compile(r"(?m)^={5,}", re.MULTILINE),                 # =====
    re.compile(r"Il giorno .{5,120}ha scritto:", re.IGNORECASE | re.DOTALL),
    re.compile(r"On .{5,120}wrote:", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?m)^Da:\s.{1,120}@", re.MULTILINE | re.IGNORECASE),
    re.compile(r"(?m)^From:\s.{1,120}@", re.MULTILINE | re.IGNORECASE),
    re.compile(r"(?m)^--\s*$", re.MULTILINE),                # firma separator
]


def _strip_quoted_reply(text: str) -> str:
    """Ritorna solo la parte NON quotata di un reply email.

    Trova il primo marcatore di testo quotato (linee con >, separatori,
    header "Da:" / "Il giorno X ha scritto:", ecc.) e tronca prima di esso.
    Se non trova marcatori, restituisce il testo invariato.
    """
    if not text:
        return text
    first_pos = len(text)
    for p in _QUOTE_START_PATTERNS:
        m = p.search(text)
        if m and m.start() < first_pos:
            first_pos = m.start()
    return text[:first_pos].strip()


_ALLEGATO_HINT_RE = re.compile(
    r"\b("
    r"in\s+allegat[oi]"
    r"|allego"
    r"|si\s+veda\s+allegat[oi]"
    r"|vedi\s+allegat[oi]"
    r"|cfr\.?\s*allegat[oi]"
    r"|trova\s+allegat[oi]"
    r"|trover[àa]\s+allegat[oi]"
    r"|in\s+attach\w*"
    r"|see\s+attach\w*"
    r"|see\s+enclos\w*"
    r"|enclosed\s+please\s+find"
    r"|please\s+find\s+attach\w*"
    r")\b",
    re.IGNORECASE,
)


def extract_text_from_pdf_bytes(
    pdf_bytes: bytes,
    max_chars: int = _PDF_CHARS_LIMIT,
) -> str:
    """Estrae il testo selezionabile da un PDF (bytes) tramite pypdf.

    Ritorna stringa vuota se:
    - `pypdf` non è installato
    - il PDF è corrotto o protetto da password
    - il PDF contiene solo immagini (niente testo selezionabile)
    """
    try:
        from pypdf import PdfReader  # type: ignore[import]
    except ImportError:
        logger.debug("pypdf non installato, skip estrazione PDF")
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            if total >= max_chars:
                break
            try:
                text = page.extract_text() or ""
                text = text.strip()
                if text:
                    parts.append(text)
                    total += len(text)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Errore estrazione pagina PDF: %s", exc)
                continue
        extracted = "\n".join(parts)[:max_chars]
        return extracted
    except Exception as exc:  # noqa: BLE001
        logger.debug("Errore lettura PDF: %s", exc)
        return ""


def extract_pdf_attachments_text(
    msg: EmailMessage,
    max_chars_per_pdf: int = _PDF_CHARS_LIMIT,
    max_attachments: int = _MAX_PDF_ATTACHMENTS,
) -> str:
    """Estrae e concatena il testo di tutti gli allegati PDF di un messaggio.

    Ritorna stringa vuota se non ci sono PDF o se non si riesce ad estrarne
    il testo. Non solleva mai eccezioni.

    Il testo viene prefissato da un separatore ``[ALLEGATO PDF: <nome>]``
    per dare contesto all'AI classificatrice.
    """
    parts: list[str] = []
    processed = 0
    try:
        for part in msg.iter_attachments():
            if processed >= max_attachments:
                break
            ctype = part.get_content_type().lower()
            filename = (part.get_filename() or "").lower()
            # Accetta application/pdf o file con estensione .pdf
            if ctype != "application/pdf" and not filename.endswith(".pdf"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes) or not payload:
                    continue
                text = extract_text_from_pdf_bytes(payload, max_chars=max_chars_per_pdf)
                if text:
                    fname_display = part.get_filename() or "allegato.pdf"
                    parts.append(f"[ALLEGATO PDF: {fname_display}]\n{text}")
                    processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("Errore processing allegato PDF %s: %s", filename, exc)
                continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("Errore iterazione allegati: %s", exc)

    return "\n\n".join(parts)


def augment_body_with_pdf(
    body_text: str,
    msg: EmailMessage,
    min_body_len: int = 200,
    max_chars_per_pdf: int = _PDF_CHARS_LIMIT,
) -> str:
    """Arricchisce `body_text` con il testo degli allegati PDF se il corpo
    è corto o assente.

    Logica:
    - Se ``len(body_text) >= min_body_len``: il corpo è già abbastanza ricco,
      non serve PDF (evita costi inutili e possibili confusioni).
    - Altrimenti: estrae il testo dai PDF allegati e lo appende (o usa come
      corpo principale se body_text è vuoto).

    Ritorna il body_text eventualmente arricchito, troncato a 8000 caratteri
    totali (limite attuale di `mail_in.body_text`).
    """
    body_hints_allegato = bool(_ALLEGATO_HINT_RE.search(body_text or ""))

    if body_hints_allegato:
        # Il body dice esplicitamente "in allegato": il contenuto classificabile
        # è il PDF, non il body. Il body (soprattutto nei reply PEC) può contenere
        # il testo quotato della nostra PEC originale senza alcun marcatore di
        # citazione — passarlo all'AI inquinerebbe la classificazione.
        # Strategia: usa SOLO il testo PDF; fallback a body stripped se il PDF
        # non ha testo selezionabile (es. scansione immagine).
        pdf_text = extract_pdf_attachments_text(msg, max_chars_per_pdf=max_chars_per_pdf)
        if pdf_text:
            logger.info(
                "PDF augmentation (allegato-hint): body scartato (%d char), "
                "uso solo PDF (%d char)",
                len(body_text), len(pdf_text),
            )
            return pdf_text[:8000]
        # PDF senza testo (scansione): prova almeno a togliere il quoted.
        stripped = _strip_quoted_reply(body_text)
        logger.info(
            "PDF augmentation (allegato-hint): PDF vuoto, body stripped %d→%d char",
            len(body_text), len(stripped),
        )
        return (stripped or body_text)[:8000]

    # Caso standard (nessun hint allegato): aggiungi PDF se il body è corto.
    if len(body_text) >= min_body_len:
        return body_text

    pdf_text = extract_pdf_attachments_text(msg, max_chars_per_pdf=max_chars_per_pdf)
    if not pdf_text:
        return body_text

    combined = f"{body_text.strip()}\n\n{pdf_text}" if body_text.strip() else pdf_text
    logger.info(
        "PDF augmentation: body_len=%d → %d (da allegato PDF)",
        len(body_text), len(combined),
    )
    return combined[:8000]
