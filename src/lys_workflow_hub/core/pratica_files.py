"""Scansione delle cartelle di una pratica WinCar (utility condivisa).

WinCar organizza i file di una pratica in due cartelle distinte:

  - ``Pratiche/<n>/Pubblici/Foto/``       immagini del veicolo / del danno
  - ``Pratiche/<n>/Pubblici/Allegati/``   documenti (denuncia, cessione, ID, ecc.)

Questo modulo legge entrambe e classifica i file in quattro categorie
(foto / denuncia / cessione / altro). E' usato sia dal workflow B
(vandalismo) sia dal workflow M4 (bozze di risposta).

La classificazione e' euristica e basata solo sul nome del file.
L'operatore puo' sempre escludere o aggiungere allegati manualmente
nella schermata di anteprima.

NB. Storicamente lo stesso modulo era duplicato in
`workflows/risarcimento_vandalismo/allegati.py`. Quel file resta come
shim/re-export per non rompere import esistenti.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


# Estensioni considerate immagini del danno.
_FOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff"}

_DENUNCIA_KEYWORDS = ("denuncia", "querela", "verbale")
_CESSIONE_KEYWORD = "cessione"

# File di sistema e cache da ignorare sempre.
_IGNORED_EXT = {".thumb", ".db", ".ini", ".tmp", ".bak", ".lnk"}
_IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_IGNORED_NAME_PARTS = (".thumb",)


def _is_ignored(file_path: Path) -> bool:
    name_lower = file_path.name.lower()
    if name_lower in _IGNORED_NAMES:
        return True
    if file_path.suffix.lower() in _IGNORED_EXT:
        return True
    if any(part in name_lower for part in _IGNORED_NAME_PARTS):
        return True
    if ".backup-" in name_lower:
        return True
    return False


@dataclass(frozen=True)
class Allegato:
    """Singolo file allegato classificato."""

    path: Path
    nome_file: str
    categoria: str  # "foto" | "denuncia" | "cessione" | "altro"
    dimensione_bytes: int
    data_modifica: date

    @property
    def size_label(self) -> str:
        kb = self.dimensione_bytes / 1024.0
        if kb < 1024:
            return f"{kb:.0f} KB"
        return f"{kb / 1024:.1f} MB"

    @property
    def estensione(self) -> str:
        return self.path.suffix.lower()


@dataclass(frozen=True)
class AllegatiPratica:
    """Allegati di una pratica gia' classificati per tipo."""

    foto: list[Allegato]
    denunce: list[Allegato]
    cessioni: list[Allegato]
    altri: list[Allegato]

    @property
    def tutti(self) -> list[Allegato]:
        out: list[Allegato] = []
        out.extend(self.cessioni)
        out.extend(self.denunce)
        out.extend(self.foto)
        out.extend(self.altri)
        return out

    @property
    def conteggio_foto(self) -> int:
        return len(self.foto)

    @property
    def ha_cessione(self) -> bool:
        return bool(self.cessioni)

    @property
    def ha_denuncia(self) -> bool:
        return bool(self.denunce)


def cartella_foto(archivio_root: Path, numero_pratica: int) -> Path:
    return (
        Path(archivio_root) / "Pratiche" / str(numero_pratica)
        / "Pubblici" / "Foto"
    )


def cartella_allegati(archivio_root: Path, numero_pratica: int) -> Path:
    return (
        Path(archivio_root) / "Pratiche" / str(numero_pratica)
        / "Pubblici" / "Allegati"
    )


# Estensioni ammesse per l'upload documenti (v3.0 fase 6): foto + formati
# ufficio comuni. Niente eseguibili/script — questi file finiscono nella
# cartella Pubblici/Allegati che WinCar mostra direttamente allo staff.
_ALLOWED_DOC_EXT = _FOTO_EXT | {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".txt", ".msg", ".eml",
}


class UploadRifiutato(ValueError):
    """File caricato dall'esterno non valido (estensione, dimensione, nome)."""


def _sanitize_filename(nome_originale: str) -> str:
    """Tiene solo il nome file (niente path traversal) e lo valida."""
    nome = Path(nome_originale.strip()).name.strip()
    if not nome or nome in {".", ".."}:
        raise UploadRifiutato("Nome file non valido.")
    return nome


# WinCar affianca a ogni foto un file <nome>.thumb (JPEG in miniatura, lato
# lungo 88px — dedotto ispezionando i file reali generati da WinCar) più un
# indice condiviso Thumbs.thumb (TIFF multi-frame, non toccato qui: formato
# fragile/proprietario, rischio di corrompere le miniature di foto già
# esistenti nella stessa cartella se scritto male — vedi CONTEXT.md).
# Generiamo solo il sidecar per-foto: file nuovo e indipendente, zero rischio
# per i dati esistenti anche se il risultato non fosse quello che WinCar si
# aspetta (nel peggiore dei casi viene semplicemente ignorato).
_WINCAR_THUMB_BOX = (88, 88)


def _genera_thumb_wincar(raw: bytes) -> bytes | None:
    """Ritorna i byte JPEG della miniatura in stile WinCar, o None se
    l'immagine non è decodificabile da Pillow (es. HEIC senza plugin) — mai
    solleva, la miniatura è un extra best-effort, non deve bloccare l'upload."""
    try:
        with Image.open(io.BytesIO(raw)) as im:
            # Molte foto da smartphone hanno un tag EXIF Orientation (il
            # sensore scatta sempre "orizzontale", la rotazione è solo
            # metadato): senza applicarlo qui il thumb esce con le
            # proporzioni scambiate (es. 88x49 invece di 49x88) — il thumb
            # non porta EXIF proprio, deve avere i pixel già corretti.
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                sfondo = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                sfondo.paste(rgba, mask=rgba.split()[-1])
                im = sfondo
            else:
                im = im.convert("RGB")
            im.thumbnail(_WINCAR_THUMB_BOX, Image.LANCZOS)
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile generare thumb WinCar: %s", exc)
        return None


def save_upload(
    *,
    archivio_root: Path,
    numero_pratica: int,
    categoria: str,
    filename: str,
    raw: bytes,
    max_bytes: int = 20 * 1024 * 1024,
) -> Path:
    """Salva un file caricato dal portale esterno nella cartella WinCar
    della pratica (``Pubblici/Foto`` o ``Pubblici/Allegati``, a seconda di
    ``categoria``), cosi' diventa visibile anche in WinCar come i file
    caricati dall'admin.

    Il nome file salvato è sempre ``<nome>_<timestamp>.<ext>`` per non
    sovrascrivere mai un file esistente (a differenza di
    ``cessione_credito/archive.py`` che usa un nome fisso + backup-rename,
    qui i nomi originali sono arbitrari e potrebbero ripetersi tra upload
    diversi dello stesso utente).
    """
    if not raw:
        raise UploadRifiutato("File vuoto.")
    if len(raw) > max_bytes:
        raise UploadRifiutato("File troppo grande (max 20 MB).")

    nome = _sanitize_filename(filename)
    ext = Path(nome).suffix.lower()

    if categoria == "foto":
        if ext not in _FOTO_EXT:
            raise UploadRifiutato(f"Formato immagine non supportato: {ext or 'sconosciuto'}.")
        target_dir = cartella_foto(archivio_root, numero_pratica)
    elif categoria == "documento":
        if ext not in _ALLOWED_DOC_EXT:
            raise UploadRifiutato(f"Formato file non supportato: {ext or 'sconosciuto'}.")
        target_dir = cartella_allegati(archivio_root, numero_pratica)
    else:
        raise ValueError(f"Categoria upload non valida: {categoria!r}")

    target_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(nome).stem
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{stem}_{timestamp}{ext}"
    # Apertura esclusiva ("xb") invece di exists()+write_bytes(): due upload
    # concorrenti con lo stesso nome file nello stesso secondo altrimenti
    # potrebbero superare entrambi il controllo di esistenza prima che uno
    # dei due scriva, con sovrascrittura silenziosa (TOCTOU).
    while True:
        try:
            with target.open("xb") as fh:
                fh.write(raw)
            break
        except FileExistsError:
            target = target_dir / f"{stem}_{timestamp}-{uuid4().hex[:6]}{ext}"

    if categoria == "foto":
        thumb_jpeg = _genera_thumb_wincar(raw)
        if thumb_jpeg is not None:
            try:
                target.with_name(target.name + ".thumb").write_bytes(thumb_jpeg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Impossibile scrivere il thumb WinCar per %s: %s", target, exc)

    return target


def _classifica_documento(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    name = file_path.name.lower()
    if ext in _FOTO_EXT:
        return "foto"
    if ext == ".pdf":
        if _CESSIONE_KEYWORD in name:
            return "cessione"
        if any(k in name for k in _DENUNCIA_KEYWORDS):
            return "denuncia"
    return "altro"


def _iter_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        if _is_ignored(entry):
            continue
        out.append(entry)
    return out


def _to_allegato(path: Path, categoria: str) -> Allegato:
    stat = path.stat()
    return Allegato(
        path=path,
        nome_file=path.name,
        categoria=categoria,
        dimensione_bytes=stat.st_size,
        data_modifica=date.fromtimestamp(stat.st_mtime),
    )


def scan(archivio_root: Path, numero_pratica: int) -> AllegatiPratica:
    """Scansiona le cartelle ``Foto/`` e ``Allegati/`` della pratica."""
    archivio_root = Path(archivio_root)

    foto: list[Allegato] = []
    denunce: list[Allegato] = []
    cessioni: list[Allegato] = []
    altri: list[Allegato] = []

    for fp in _iter_files(cartella_foto(archivio_root, numero_pratica)):
        foto.append(_to_allegato(fp, "foto"))

    for fp in _iter_files(cartella_allegati(archivio_root, numero_pratica)):
        categoria = _classifica_documento(fp)
        item = _to_allegato(fp, categoria)
        if categoria == "foto":
            foto.append(item)
        elif categoria == "cessione":
            cessioni.append(item)
        elif categoria == "denuncia":
            denunce.append(item)
        else:
            altri.append(item)

    cessioni.sort(key=lambda a: a.nome_file, reverse=True)

    return AllegatiPratica(foto=foto, denunce=denunce, cessioni=cessioni, altri=altri)


def filtra_per_nome(
    allegati: AllegatiPratica, nomi_selezionati: Iterable[str]
) -> list[Allegato]:
    """Restituisce solo gli allegati i cui nomi file sono nella lista data."""
    selezionati = {n.strip() for n in nomi_selezionati if n and n.strip()}
    return [a for a in allegati.tutti if a.nome_file in selezionati]


__all__ = [
    "Allegato",
    "AllegatiPratica",
    "UploadRifiutato",
    "cartella_foto",
    "cartella_allegati",
    "scan",
    "filtra_per_nome",
    "save_upload",
]
