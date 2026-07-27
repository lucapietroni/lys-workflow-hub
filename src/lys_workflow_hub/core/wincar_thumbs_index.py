"""Genera/aggiorna ``Thumbs.thumb``, l'indice di miniature condiviso per
cartella che WinCar usa per mostrare le foto nella propria UI.

Formato (nessuna documentazione ufficiale, dedotto ispezionando file reali
generati da WinCar): TIFF multi-frame little-endian, un frame per foto della
cartella, ciascuno con lo stream JPEG della miniatura incapsulato in stile
"vecchio" TIFF/JPEG (tag 512-515, offset assoluti nel file) — con
compressione dichiarata 7 anziché 6, verosimile artefatto del codec TIFF di
.NET/GDI+ (che scrive quel valore pur usando lo schema "old-style"). Ogni
frame ha esattamente 24 entry IFD nello stesso ordine/tipo osservato nei
file reali — vedi CONTEXT.md per il dettaglio del reverse engineering.

ATTENZIONE — è un indice CONDIVISO per l'intera cartella (una foto = un
frame), non un file per foto: un bug qui rischia di rendere illeggibili le
miniature di TUTTE le foto della pratica, non solo quella appena caricata.
Per questo l'append non ricodifica MAI i frame esistenti: si limita a (a)
patchare in-place, nel proprio buffer in memoria, i 4 byte del puntatore
"next IFD" dell'ultimo frame esistente e (b) accodare in fondo i dati del
nuovo frame — poi scrive tutto su un file temporaneo e lo rinomina sopra
l'originale (mai un write parziale in-place sul file vero). Verificato
byte-per-byte contro un Thumbs.thumb reale: dopo un append, ogni singolo
byte dei frame preesistenti risulta identico all'originale.
"""
from __future__ import annotations

import struct
from pathlib import Path

_TIFF_HEADER = b"II\x2a\x00"

_TAG_NEW_SUBFILE_TYPE = 254
_TAG_IMAGE_WIDTH = 256
_TAG_IMAGE_LENGTH = 257
_TAG_BITS_PER_SAMPLE = 258
_TAG_COMPRESSION = 259
_TAG_PHOTOMETRIC = 262
_TAG_IMAGE_DESCRIPTION = 270
_TAG_STRIP_OFFSETS = 273
_TAG_ORIENTATION = 274
_TAG_SAMPLES_PER_PIXEL = 277
_TAG_ROWS_PER_STRIP = 278
_TAG_STRIP_BYTE_COUNTS = 279
_TAG_X_RESOLUTION = 282
_TAG_Y_RESOLUTION = 283
_TAG_PLANAR_CONFIG = 284
_TAG_RESOLUTION_UNIT = 296
_TAG_PAGE_NUMBER = 297
_TAG_JPEG_PROC = 512
_TAG_JPEG_IF_OFFSET = 513
_TAG_JPEG_IF_LENGTH = 514
_TAG_JPEG_RESTART = 515
_TAG_YCBCR_SUBSAMPLING = 530
_TAG_YCBCR_POSITIONING = 531
_TAG_REFERENCE_BLACK_WHITE = 532

_TYPE_ASCII = 2
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_RATIONAL = 5

# Il JPEG della miniatura DEVE essere codificato con questo stesso
# sottocampionamento (PIL subsampling=1 -> fattori 2,1): il tag TIFF
# YCbCrSubSampling qui sotto è hardcoded e deve combaciare con quello reale
# dei byte JPEG incapsulati, altrimenti il decoder TIFF (libtiff/GDI+) fallisce
# sul frame nuovo pur restando il file strutturalmente valido — vedi
# `pratica_files._genera_thumb_wincar`, che usa `subsampling=1` per questo.
_YCBCR_SUBSAMPLING = (2, 1)


def _entry(tag: int, typ: int, count: int, value_bytes: bytes) -> bytes:
    """Una entry IFD da 12 byte: tag(2) type(2) count(4) value/offset(4)."""
    return struct.pack("<HHI", tag, typ, count) + value_bytes


def _inline_short(v: int) -> bytes:
    return struct.pack("<HH", v, 0)


def _inline_long(v: int) -> bytes:
    return struct.pack("<I", v)


def _offset(v: int) -> bytes:
    return struct.pack("<I", v)


def _costruisci_frame(
    *, nome_thumb: str, jpeg: bytes, width: int, height: int, frame_index: int, base_offset: int
) -> tuple[bytes, int]:
    """Costruisce il blob (valori esterni + JPEG + descrizione + IFD) di un
    frame, pronto per essere accodato a partire da `base_offset` (posizione
    assoluta che avrà nel file finale). Ritorna (blob, offset_assoluto_IFD)."""
    descr = (nome_thumb + "\x00").encode("ascii", errors="replace")

    bits_per_sample = struct.pack("<HHH", 8, 8, 8)
    reference_bw = struct.pack("<12I", 0, 1, 255, 1, 128, 1, 255, 1, 128, 1, 255, 1)
    x_res = struct.pack("<II", 150, 1)
    y_res = struct.pack("<II", 150, 1)

    off_bps = base_offset
    off_refbw = off_bps + len(bits_per_sample)
    off_xres = off_refbw + len(reference_bw)
    off_yres = off_xres + len(x_res)
    off_jpeg = off_yres + len(y_res)
    off_descr = off_jpeg + len(jpeg)
    off_ifd = off_descr + len(descr)
    if off_ifd % 2 != 0:
        off_ifd += 1  # allineamento a word, come nei file WinCar reali
    padding = b"\x00" * (off_ifd - (off_descr + len(descr)))

    ss_x, ss_y = _YCBCR_SUBSAMPLING
    entries = [
        _entry(_TAG_NEW_SUBFILE_TYPE, _TYPE_LONG, 1, _inline_long(2)),
        _entry(_TAG_IMAGE_WIDTH, _TYPE_LONG, 1, _inline_long(width)),
        _entry(_TAG_IMAGE_LENGTH, _TYPE_LONG, 1, _inline_long(height)),
        _entry(_TAG_BITS_PER_SAMPLE, _TYPE_SHORT, 3, _offset(off_bps)),
        _entry(_TAG_COMPRESSION, _TYPE_SHORT, 1, _inline_short(7)),
        _entry(_TAG_PHOTOMETRIC, _TYPE_SHORT, 1, _inline_short(6)),  # YCbCr
        _entry(_TAG_IMAGE_DESCRIPTION, _TYPE_ASCII, len(descr), _offset(off_descr)),
        _entry(_TAG_STRIP_OFFSETS, _TYPE_LONG, 1, _inline_long(off_jpeg)),
        _entry(_TAG_ORIENTATION, _TYPE_SHORT, 1, _inline_short(1)),
        _entry(_TAG_SAMPLES_PER_PIXEL, _TYPE_SHORT, 1, _inline_short(3)),
        _entry(_TAG_ROWS_PER_STRIP, _TYPE_SHORT, 1, _inline_short(height)),
        _entry(_TAG_STRIP_BYTE_COUNTS, _TYPE_LONG, 1, _inline_long(len(jpeg))),
        _entry(_TAG_X_RESOLUTION, _TYPE_RATIONAL, 1, _offset(off_xres)),
        _entry(_TAG_Y_RESOLUTION, _TYPE_RATIONAL, 1, _offset(off_yres)),
        _entry(_TAG_PLANAR_CONFIG, _TYPE_SHORT, 1, _inline_short(1)),
        _entry(_TAG_RESOLUTION_UNIT, _TYPE_SHORT, 1, _inline_short(2)),
        _entry(_TAG_PAGE_NUMBER, _TYPE_SHORT, 2, struct.pack("<HH", frame_index, 0)),
        _entry(_TAG_JPEG_PROC, _TYPE_SHORT, 1, _inline_short(1)),
        _entry(_TAG_JPEG_IF_OFFSET, _TYPE_LONG, 1, _inline_long(off_jpeg)),
        _entry(_TAG_JPEG_IF_LENGTH, _TYPE_LONG, 1, _inline_long(len(jpeg))),
        _entry(_TAG_JPEG_RESTART, _TYPE_SHORT, 1, _inline_short(0)),
        _entry(_TAG_YCBCR_SUBSAMPLING, _TYPE_SHORT, 2, struct.pack("<HH", ss_x, ss_y)),
        _entry(_TAG_YCBCR_POSITIONING, _TYPE_SHORT, 1, _inline_short(2)),
        _entry(_TAG_REFERENCE_BLACK_WHITE, _TYPE_RATIONAL, 6, _offset(off_refbw)),
    ]
    ifd = struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)

    blob = bits_per_sample + reference_bw + x_res + y_res + jpeg + descr + padding + ifd
    return blob, off_ifd


# Limite di sicurezza sul numero di frame attesi in catena: non un valore
# realistico (nessuna pratica ha migliaia di foto), solo una guardia
# anti-loop-infinito se la catena IFD risultasse malformata/ciclica.
_MAX_FRAME_CHAIN = 20_000


class ThumbsIndexError(Exception):
    """Thumbs.thumb esistente non è nel formato atteso (header o catena IFD
    malformata) — l'append viene rifiutato invece di rischiare un loop
    infinito o dati incoerenti."""


def _walk_ifd_chain(data: bytes) -> tuple[int, int]:
    """Ritorna (numero di frame, offset del campo "next IFD" dell'ultimo
    frame) percorrendo la catena. Solleva ThumbsIndexError se l'header non è
    quello atteso o se la catena non è strettamente crescente (offset non
    crescente = ciclo o riferimento a dati già scritti, impossibile per un
    file scritto solo in append da questo modulo) — mai un loop infinito.
    """
    if data[:4] != _TIFF_HEADER:
        raise ThumbsIndexError(f"Header TIFF inatteso: {data[:4]!r}")

    frame_count = 0
    ifd_offset = struct.unpack_from("<I", data, 4)[0]
    last_next_field = None
    offset_minimo = 8  # nessun IFD può stare dentro l'header di 8 byte
    while ifd_offset != 0:
        if ifd_offset < offset_minimo or ifd_offset + 2 > len(data):
            raise ThumbsIndexError(f"Offset IFD fuori range/non crescente: {ifd_offset}")
        if frame_count >= _MAX_FRAME_CHAIN:
            raise ThumbsIndexError(f"Catena IFD oltre {_MAX_FRAME_CHAIN} frame, probabile ciclo")
        count = struct.unpack_from("<H", data, ifd_offset)[0]
        next_field = ifd_offset + 2 + count * 12
        if next_field + 4 > len(data):
            raise ThumbsIndexError(f"Entry IFD oltre la fine del file: {next_field}")
        next_ifd = struct.unpack_from("<I", data, next_field)[0]
        last_next_field = next_field
        frame_count += 1
        offset_minimo = ifd_offset + 1  # il prossimo IFD deve stare più avanti
        ifd_offset = next_ifd

    return frame_count, last_next_field


def aggiorna_indice_thumbs(
    path: Path, *, nome_thumb: str, jpeg: bytes, width: int, height: int
) -> None:
    """Aggiunge un frame a `Thumbs.thumb` (append se esiste già — preserva
    intatti tutti i frame preesistenti — altrimenti lo crea da zero).

    Solleva `ThumbsIndexError` se il file esistente non è nel formato
    atteso (mai un loop infinito, mai una scrittura su dati che non riusciamo
    a interpretare) — il chiamante (`save_upload`) la cattura come qualunque
    altro errore di questo passaggio best-effort.

    Nota di concorrenza: legge-modifica-riscrive l'intero file, senza
    locking. Due upload sulla STESSA cartella in corsa (richieste HTTP
    distinte, es. admin + portale sulla stessa pratica, o un batch di più
    file per richiesta) possono sovrascriversi: l'ultimo write vince e il
    frame dell'altro upload va PERSO in modo permanente (non c'è un
    meccanismo che lo rigeneri al giro successivo — un upload successivo
    aggiunge solo il proprio frame). Rischio residuo accettato per ora
    (singola carrozzeria); se dovesse servire, la soluzione sarebbe un lock
    per-cartella attorno a questa funzione.
    """
    if path.exists():
        data = bytearray(path.read_bytes())
        frame_count, last_next_field = _walk_ifd_chain(data)
        base_offset = len(data)
        blob, ifd_abs = _costruisci_frame(
            nome_thumb=nome_thumb, jpeg=jpeg, width=width, height=height,
            frame_index=frame_count, base_offset=base_offset,
        )
        struct.pack_into("<I", data, last_next_field, ifd_abs)
        data += blob
    else:
        header = bytearray(_TIFF_HEADER + struct.pack("<I", 0))
        blob, ifd_abs = _costruisci_frame(
            nome_thumb=nome_thumb, jpeg=jpeg, width=width, height=height,
            frame_index=0, base_offset=len(header),
        )
        struct.pack_into("<I", header, 4, ifd_abs)
        data = header + blob

    tmp_path = path.parent / (path.name + ".tmp")
    try:
        tmp_path.write_bytes(bytes(data))
        tmp_path.replace(path)
    finally:
        # replace() sposta/rinomina il file: se ha già avuto successo
        # tmp_path non esiste più e unlink(missing_ok=True) non fa nulla; se
        # ha sollevato (es. Windows con Thumbs.thumb aperto da WinCar senza
        # FILE_SHARE_DELETE) ripuliamo il temporaneo invece di lasciarlo lì.
        tmp_path.unlink(missing_ok=True)
