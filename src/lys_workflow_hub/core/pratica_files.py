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

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


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
    "cartella_foto",
    "cartella_allegati",
    "scan",
    "filtra_per_nome",
]
