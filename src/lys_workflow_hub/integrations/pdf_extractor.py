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
from email.message import EmailMessage


logger = logging.getLogger(__name__)

# Numero massimo di caratteri estratti da un singolo PDF.
_PDF_CHARS_LIMIT = 4000

# Numero massimo di PDF allegati da processare per mail (per sicurezza).
_MAX_PDF_ATTACHMENTS = 3


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
    if len(body_text) >= min_body_len:
        return body_text

    pdf_text = extract_pdf_attachments_text(msg, max_chars_per_pdf=max_chars_per_pdf)
    if not pdf_text:
        return body_text

    if body_text.strip():
        combined = f"{body_text.strip()}\n\n{pdf_text}"
    else:
        combined = pdf_text

    logger.info(
        "PDF augmentation: body_len=%d → %d (da allegato PDF)",
        len(body_text), len(combined),
    )
    return combined[:8000]
