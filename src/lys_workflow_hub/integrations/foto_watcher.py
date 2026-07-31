"""File watcher per le foto lavorazioni da inbox Syncthing.

Flusso automatico (zero azioni operative):
  1. Syncthing deposita foto da smartphone in foto_inbox_path
  2. Watchdog rileva il file → coda thread-safe
  3. Worker: Claude Vision estrae targa → copia in fallback + pratica WinCar → log DB → elimina inbox

Lettura targa a due passaggi:
  - 1° tentativo su foto intera. Se la targa è piccola/distante/capovolta e
    l'immagine non viene letta con certezza, il modello restituisce comunque
    la zona approssimativa (REGIONE) dove pensa ci sia una targa.
  - Se c'è una REGIONE, ritaglio quella zona dall'originale a piena
    risoluzione (Pillow) e ritento: l'API Anthropic ridimensiona sempre le
    immagini a un lato lungo ~1568px, quindi su foto d'insieme il dettaglio
    di una targa piccola va perso nel resize anche se l'originale è ad alta
    risoluzione — il ritaglio aggira il problema.

Regola destinazione:
  - SEMPRE in foto_fallback_path/<TARGA>/  (o /SCONOSCIUTA/ se targa non letta)
  - SE pratica trovata in WinCar → ANCHE in Pratiche/<n>/Pubblici/Foto/

Formati supportati: .jpg .jpeg .png .webp
HEIC (default iPhone): loggato come errore + saltato.
  → Configurare lo smartphone: Impostazioni → Fotocamera → Formato → Compatibilità massima
"""
from __future__ import annotations

import base64
import io
import logging
import queue
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import anthropic

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _WATCHDOG_OK = True
except ImportError:
    _WATCHDOG_OK = False
    # Stub minimali: permettono l'import del modulo anche senza watchdog installato.
    # Lo start() fallirà con messaggio chiaro.
    class FileSystemEventHandler:  # type: ignore[no-redef]
        pass
    class Observer:  # type: ignore[no-redef]
        pass

try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository
from lys_workflow_hub.core.pratica_files import cartella_foto
from lys_workflow_hub.core.wincar_repository import WinCarRepository

logger = logging.getLogger(__name__)

_TARGA_SEARCH_RE = re.compile(r"TARGA:\s*([A-Z]{2}\d{3}[A-Z]{2})\b")
_REGIONE_RE = re.compile(
    r"REGIONE:\s*(\d{1,3}(?:\.\d+)?)\s*,\s*(\d{1,3}(?:\.\d+)?)\s*,\s*"
    r"(\d{1,3}(?:\.\d+)?)\s*,\s*(\d{1,3}(?:\.\d+)?)"
)
# Targhe mai emesse in Italia (serie non ancora raggiunta): se il modello le
# restituisce è quasi certamente un'allucinazione da esempio/placeholder,
# non una lettura reale.
_TARGA_BLACKLIST = {"AA000AA", "AA111AA", "XX000XX"}
_SUPPORTED: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_MAX_IMG_BYTES = 20 * 1024 * 1024  # 20 MB
_AI_TIMEOUT = 30.0                  # secondi per chiamata Claude Vision

_PROMPT_LETTURA = (
    "Individua la targa italiana del veicolo in foto. Schema targa "
    "italiana standard: due lettere maiuscole, poi tre cifre, poi due "
    "lettere maiuscole, senza spazi (es. due lettere-tre cifre-due "
    "lettere).\n"
    "La foto può essere scattata di taglio, angolata, con riflessi, "
    "parzialmente in ombra, o la targa può risultare fisicamente "
    "capovolta/ruotata di 180° (es. portellone posteriore aperto oltre "
    "la verticale): usa la spaziatura tipica e la forma dei caratteri "
    "per dedurli anche se deformati, inclinati o capovolti. Attenzione "
    "a caratteri facilmente confondibili, specie se l'immagine è "
    "ruotata: 0/O, 1/I, 8/B, 5/S, 2/Z, G/C.\n"
    "Rispondi in questo formato, tre righe esatte:\n"
    "RAGIONAMENTO: <breve descrizione di cosa vedi e come hai letto i "
    "caratteri>\n"
    "TARGA: <la targa letta, oppure NONE se non riesci a leggere con "
    "certezza una targa italiana valida nell'immagine>\n"
    "REGIONE: <coordinate percentuali x1,y1,x2,y2 (0-100, origine in alto "
    "a sinistra) del rettangolo che contiene la targa o un oggetto simile "
    "a una targa, anche se non l'hai letta con certezza — utile per "
    "ritagliare e ingrandire quella zona; oppure NONE se non vedi nessuna "
    "targa né rettangolo plausibile in tutta l'immagine>\n"
    "Non inventare né dedurre una targa se non è visibile: in quel caso "
    "rispondi sempre NONE alla riga TARGA."
)

_PROMPT_ZOOM = (
    "Questo è un ritaglio ravvicinato e ingrandito di una foto più ampia, "
    "centrato su una zona che potrebbe contenere una targa italiana "
    "(schema: due lettere maiuscole, tre cifre, due lettere maiuscole). "
    "Può essere ancora angolata, di taglio o capovolta di 180°. Leggi i "
    "caratteri con attenzione, specie quelli facilmente confondibili se "
    "ruotata o poco nitida: 0/O, 1/I, 8/B, 5/S, 2/Z, G/C.\n"
    "Rispondi in questo formato, due righe esatte:\n"
    "RAGIONAMENTO: <breve descrizione di cosa vedi e come hai letto i "
    "caratteri>\n"
    "TARGA: <la targa letta, oppure NONE se anche qui non è leggibile con "
    "certezza>"
)


def _dest_name(original: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    return f"{ts}_{uid}_{original.name}"


class _QueueingHandler(FileSystemEventHandler):
    """Mette i path dei nuovi file nella coda; non fa elaborazione."""

    def __init__(self, q: "queue.Queue[str]") -> None:
        self._q = q

    def on_created(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._q.put(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[override]
        # Syncthing scrive su file temp e poi rinomina → intercettiamo il dest
        if not event.is_directory:
            self._q.put(event.dest_path)


class FotoWatcher:
    """Avvia observer watchdog + worker thread per elaborazione foto automatica."""

    def __init__(
        self,
        settings: Settings,
        foto_repo: FotoLavorazioniRepository,
    ) -> None:
        self._settings = settings
        self._foto_repo = foto_repo
        self._q: "queue.Queue[str]" = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._observer: Observer | None = None
        self._worker: threading.Thread | None = None
        # Client Anthropic condiviso (un solo httpx pool per l'intera vita del watcher)
        self._ai_client: anthropic.Anthropic | None = (
            anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=_AI_TIMEOUT)
            if settings.anthropic_api_key
            else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not _WATCHDOG_OK:
            raise RuntimeError(
                "watchdog non installato. Eseguire: pip install watchdog>=4.0"
            )
        if not self._settings.foto_inbox_path:
            raise RuntimeError("foto_inbox_path non configurato")

        inbox = Path(self._settings.foto_inbox_path)
        inbox.mkdir(parents=True, exist_ok=True)

        # Accoda file già presenti (sopravvissuti a crash precedente)
        pre_existing = [f for f in inbox.iterdir() if f.is_file()]
        for f in pre_existing:
            try:
                self._q.put_nowait(str(f))
            except queue.Full:
                logger.warning("FotoWatcher: coda piena, saltato file pre-esistente %s", f.name)
        if pre_existing:
            logger.info("FotoWatcher: accodati %d file pre-esistenti dall'inbox", len(pre_existing))

        handler = _QueueingHandler(self._q)
        self._observer = Observer()
        self._observer.schedule(handler, str(inbox), recursive=False)
        self._observer.start()
        logger.info("FotoWatcher: osservando inbox %s", inbox)

        self._worker = threading.Thread(
            target=self._run, daemon=True, name="foto-worker"
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        if self._worker:
            self._worker.join(timeout=5)
        if self._ai_client:
            self._ai_client.close()
        logger.info("FotoWatcher: fermato")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._process(Path(raw))
            except Exception:
                logger.exception("FotoWatcher: errore non gestito su %s", raw)

    def _process(self, path: Path) -> None:
        time.sleep(2)  # attesa scrittura completa (Syncthing rename è atomico, ma per sicurezza)
        if not path.exists():
            return

        ext = path.suffix.lower()
        filename = path.name

        if "trashed" in filename.lower():
            # Cestino Android (.trashed-<timestamp>-<nome>) sincronizzato per errore
            # da Syncthing: foto già cancellate dall'utente, spesso non auto.
            logger.info("FotoWatcher: %s dal cestino Android, ignorato", filename)
            try:
                path.unlink()
            except OSError:
                pass
            return

        if ext == ".heic":
            logger.warning(
                "FotoWatcher: HEIC non supportato (%s). "
                "Configurare fotocamera: Impostazioni → Fotocamera → Formato → Compatibilità massima",
                filename,
            )
            self._foto_repo.log_foto(
                filename=filename,
                stato="heic",
                errore="HEIC non supportato da Claude Vision",
            )
            return

        media_type = _SUPPORTED.get(ext)
        if not media_type:
            return  # file di sistema o formato non immagine

        # Controllo dimensione prima di caricare in RAM
        try:
            file_size = path.stat().st_size
        except OSError:
            return
        if file_size > _MAX_IMG_BYTES:
            logger.error(
                "FotoWatcher: %s troppo grande (%d MB, max %d MB), saltato",
                filename, file_size // 1_000_000, _MAX_IMG_BYTES // 1_000_000,
            )
            self._foto_repo.log_foto(
                filename=filename,
                stato="errore",
                errore=f"File troppo grande ({file_size // 1_000_000} MB, max {_MAX_IMG_BYTES // 1_000_000} MB)",
            )
            return

        try:
            img_bytes = path.read_bytes()
        except OSError as exc:
            logger.error("FotoWatcher: impossibile leggere %s: %s", path, exc)
            self._foto_repo.log_foto(filename=filename, stato="errore", errore=str(exc))
            return

        # 1. Estrai targa via Claude Vision
        targa = self._extract_targa(img_bytes, media_type)

        # 2. Copia sempre in fallback
        targa_dir = targa if targa else "SCONOSCIUTA"
        fallback_dir = Path(self._settings.foto_fallback_path) / targa_dir
        fallback_dir.mkdir(parents=True, exist_ok=True)
        dest_fallback = fallback_dir / _dest_name(path)
        shutil.copy2(path, dest_fallback)
        logger.info("FotoWatcher: %s → fallback %s", filename, dest_fallback)

        # 3. Copia anche in pratica WinCar (se targa trovata e copia abilitata)
        pratica_numero: int | None = None
        dest_pratica_str = ""
        if targa and self._foto_repo.get_copia_pratica_abilitata():
            try:
                wincar = WinCarRepository.from_settings()
                results = wincar.search_pratiche(targa=targa, limit=1)
                if results:
                    pratica_numero = results[0].numero
                    foto_dir = cartella_foto(self._settings.wincar_archivio, pratica_numero)
                    foto_dir.mkdir(parents=True, exist_ok=True)
                    dest_pratica = foto_dir / _dest_name(path)
                    shutil.copy2(path, dest_pratica)
                    dest_pratica_str = str(dest_pratica)
                    logger.info(
                        "FotoWatcher: %s → pratica %d (%s)",
                        filename, pratica_numero, dest_pratica,
                    )
                else:
                    logger.info("FotoWatcher: targa %s → nessuna pratica WinCar trovata", targa)
            except Exception:
                logger.exception("FotoWatcher: errore ricerca pratica per targa %s", targa)

        # 4. Log DB prima di eliminare: se unlink fallisce il record c'è già
        if targa is None:
            stato = "targa_non_trovata"
        elif pratica_numero:
            stato = "ok"
        else:
            stato = "ok_no_pratica"

        self._foto_repo.log_foto(
            filename=filename,
            targa=targa or "",
            pratica_numero=pratica_numero,
            percorso_fallback=str(dest_fallback),
            percorso_pratica=dest_pratica_str,
            stato=stato,
        )

        # 5. Elimina dall'inbox
        try:
            path.unlink()
        except OSError:
            logger.warning("FotoWatcher: impossibile eliminare %s dall'inbox", path)

    # ------------------------------------------------------------------
    # Claude Vision
    # ------------------------------------------------------------------

    def _extract_targa(self, img_bytes: bytes, media_type: str) -> str | None:
        if self._ai_client is None:
            logger.warning(
                "FotoWatcher: ANTHROPIC_API_KEY non configurata, targa non estratta"
            )
            return None

        text = self._call_vision(img_bytes, media_type, _PROMPT_LETTURA)
        if text is None:
            return None

        targa = self._parse_targa(text)
        if targa:
            return targa

        # Targa non letta con certezza sull'immagine intera: l'API Anthropic
        # ridimensiona sempre le immagini a un lato lungo ~1568px prima di
        # "vederle". Su foto d'insieme (targa piccola/distante/capovolta) il
        # dettaglio va perso nel resize anche se l'originale è ad alta
        # risoluzione. Se il primo passaggio ha comunque individuato una zona
        # plausibile, ritagliamo quella zona dall'originale a piena
        # risoluzione e ritentiamo: la targa occuperà quasi tutto il frame.
        if not _PIL_OK:
            return None
        regione = self._parse_regione(text)
        if regione is None:
            return None
        crop_bytes = self._crop_region(img_bytes, regione)
        if crop_bytes is None:
            return None
        logger.info("FotoWatcher: targa non letta su foto intera, ritento su ritaglio zona %s", regione)
        text2 = self._call_vision(crop_bytes, "image/jpeg", _PROMPT_ZOOM)
        if text2 is None:
            return None
        return self._parse_targa(text2)

    def _call_vision(self, img_bytes: bytes, media_type: str, prompt: str) -> str | None:
        try:
            b64 = base64.standard_b64encode(img_bytes).decode()
            msg = self._ai_client.messages.create(
                model=self._settings.anthropic_vision_model,
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            testo = next((b.text for b in msg.content if b.type == "text"), None)
            if testo is None:
                logger.warning(
                    "FotoWatcher: risposta Claude Vision senza blocco testo (tipi: %s)",
                    [b.type for b in msg.content],
                )
                return None
            return testo.strip().upper()
        except Exception:
            logger.exception("FotoWatcher: errore Claude Vision")
            return None

    @staticmethod
    def _parse_targa(text: str) -> str | None:
        match = _TARGA_SEARCH_RE.search(text)
        if not match:
            logger.info("FotoWatcher: Claude Vision → nessuna targa (risposta: %r)", text)
            return None
        targa = match.group(1)
        if targa in _TARGA_BLACKLIST:
            logger.warning(
                "FotoWatcher: targa %s in blacklist (probabile allucinazione da "
                "placeholder), scartata (risposta: %r)", targa, text,
            )
            return None
        logger.info("FotoWatcher: Claude Vision → targa %s (risposta: %r)", targa, text)
        return targa

    @staticmethod
    def _parse_regione(text: str) -> tuple[float, float, float, float] | None:
        match = _REGIONE_RE.search(text)
        if not match:
            if "REGIONE:" in text:
                logger.warning(
                    "FotoWatcher: REGIONE presente ma non parsabile (risposta: %r)", text,
                )
            return None
        x1, y1, x2, y2 = (float(g) for g in match.groups())
        if not all(0 <= v <= 100 for v in (x1, y1, x2, y2)):
            logger.warning("FotoWatcher: REGIONE fuori range 0-100: %s", match.groups())
            return None
        if x2 <= x1 or y2 <= y1:
            logger.warning("FotoWatcher: REGIONE con box degenere: %s", match.groups())
            return None
        return (x1, y1, x2, y2)

    @staticmethod
    def _crop_region(
        img_bytes: bytes,
        box_pct: tuple[float, float, float, float],
        pad_pct: float = 8.0,
    ) -> bytes | None:
        try:
            im = Image.open(io.BytesIO(img_bytes))
            im.load()
            w, h = im.size
            x1p, y1p, x2p, y2p = box_pct
            pad_x = (x2p - x1p) * pad_pct / 100
            pad_y = (y2p - y1p) * pad_pct / 100
            x1 = max(0.0, x1p - pad_x) / 100 * w
            x2 = min(100.0, x2p + pad_x) / 100 * w
            y1 = max(0.0, y1p - pad_y) / 100 * h
            y2 = min(100.0, y2p + pad_y) / 100 * h
            crop = im.crop((int(x1), int(y1), int(x2), int(y2)))
            if crop.width < 10 or crop.height < 10:
                return None
            if crop.width < 700:
                scale = 700 / crop.width
                crop = crop.resize(
                    (int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS
                )
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:
            logger.exception("FotoWatcher: errore ritaglio zona targa")
            return None
