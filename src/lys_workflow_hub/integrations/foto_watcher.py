"""File watcher per le foto lavorazioni da inbox Syncthing.

Flusso automatico (zero azioni operative):
  1. Syncthing deposita foto da smartphone in foto_inbox_path
  2. Watchdog rileva il file → coda thread-safe
  3. Worker: Claude Vision estrae targa → copia in fallback + pratica WinCar → log DB → elimina inbox

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

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository
from lys_workflow_hub.core.pratica_files import cartella_foto
from lys_workflow_hub.core.wincar_repository import WinCarRepository

logger = logging.getLogger(__name__)

_TARGA_SEARCH_RE = re.compile(r"\b([A-Z]{2}\d{3}[A-Z]{2})\b")
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
                            {
                                "type": "text",
                                "text": (
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
                                    "Rispondi in questo formato, due righe esatte:\n"
                                    "RAGIONAMENTO: <breve descrizione di cosa vedi e come hai letto i "
                                    "caratteri>\n"
                                    "TARGA: <la targa letta, oppure NONE se non riesci a leggere con "
                                    "certezza una targa italiana valida nell'immagine>\n"
                                    "Non inventare né dedurre una targa se non è visibile: in quel "
                                    "caso rispondi sempre NONE."
                                ),
                            },
                        ],
                    }
                ],
            )
            text = msg.content[0].text.strip().upper()
            match = _TARGA_SEARCH_RE.search(text)
            if match:
                targa = match.group(1)
                if targa in _TARGA_BLACKLIST:
                    logger.warning(
                        "FotoWatcher: targa %s in blacklist (probabile allucinazione "
                        "da placeholder), scartata (risposta: %r)", targa, text,
                    )
                    return None
                logger.info("FotoWatcher: Claude Vision → targa %s (risposta: %r)", targa, text)
                return targa
            logger.info("FotoWatcher: Claude Vision → nessuna targa (risposta: %r)", text)
            return None
        except Exception:
            logger.exception("FotoWatcher: errore Claude Vision")
            return None
