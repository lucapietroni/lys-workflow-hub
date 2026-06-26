"""File watcher per le foto lavorazioni da inbox Syncthing.

Flusso automatico (zero azioni operative):
  1. Syncthing deposita foto da smartphone in foto_inbox_path
  2. Watchdog rileva il file → coda thread-safe
  3. Worker: Claude Vision estrae targa → copia in fallback + pratica WinCar → elimina inbox → log DB

Regola destinazione:
  - SEMPRE in foto_fallback_path/<TARGA>/  (o /SCONOSCIUTA/ se targa non letta)
  - SE pratica trovata in WinCar → ANCHE in Pratiche/<n>/Pubblici/Foto/

Formati supportati: .jpg .jpeg .png .webp
HEIC (default iPhone): loggato come errore + saltato.
  → Configurare lo smartphone: Impostazioni → Fotocamera → Formato → Compatibilità massima
"""
from __future__ import annotations

import base64
import logging
import queue
import re
import shutil
import threading
import time
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

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository
from lys_workflow_hub.core.pratica_files import cartella_foto
from lys_workflow_hub.core.wincar_repository import WinCarRepository

logger = logging.getLogger(__name__)

_TARGA_RE = re.compile(r"^[A-Z]{2}\d{3}[A-Z]{2}$")
_SUPPORTED: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _dest_name(original: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{original.name}"


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
        self._q: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._observer: Observer | None = None
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not _WATCHDOG_OK:
            raise RuntimeError(
                "watchdog non installato. Eseguire: pip install watchdog>=4.0"
            )
        inbox = Path(self._settings.foto_inbox_path)
        inbox.mkdir(parents=True, exist_ok=True)

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

        # 3. Copia anche in pratica WinCar (se targa trovata)
        pratica_numero: int | None = None
        dest_pratica_str = ""
        if targa:
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

        # 4. Elimina dall'inbox
        try:
            path.unlink()
        except OSError:
            logger.warning("FotoWatcher: impossibile eliminare %s dall'inbox", path)

        # 5. Log
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

    # ------------------------------------------------------------------
    # Claude Vision
    # ------------------------------------------------------------------

    def _extract_targa(self, img_bytes: bytes, media_type: str) -> str | None:
        if not self._settings.anthropic_api_key:
            logger.warning(
                "FotoWatcher: ANTHROPIC_API_KEY non configurata, targa non estratta"
            )
            return None
        try:
            client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            b64 = base64.standard_b64encode(img_bytes).decode()
            msg = client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=20,
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
                            {
                                "type": "text",
                                "text": (
                                    "Estrai la targa del veicolo italiano dall'immagine. "
                                    "Rispondi SOLO con la targa nel formato esatto AA000AA "
                                    "(2 lettere maiuscole, 3 cifre, 2 lettere maiuscole), senza spazi. "
                                    "Se non vedi una targa italiana leggibile, rispondi esattamente: NONE"
                                ),
                            },
                        ],
                    }
                ],
            )
            text = msg.content[0].text.strip().upper()
            if _TARGA_RE.match(text):
                logger.info("FotoWatcher: Claude Vision → targa %s", text)
                return text
            logger.info("FotoWatcher: Claude Vision → nessuna targa (risposta: %r)", text)
            return None
        except Exception:
            logger.exception("FotoWatcher: errore Claude Vision")
            return None
