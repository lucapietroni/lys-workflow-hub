"""Test per `wincar_thumbs_index.py` (indice miniature Thumbs.thumb di WinCar).

Verifica byte-per-byte che un append non tocchi mai i frame preesistenti —
è la garanzia di sicurezza centrale di questo modulo, dato che Thumbs.thumb
è un indice CONDIVISO per l'intera cartella (un bug qui rischierebbe di
rendere illeggibili le miniature di foto già esistenti, non solo quella
nuova). Non usa dati reali di pratiche/clienti: i JPEG di test sono
generati al volo con Pillow.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

import pytest

from lys_workflow_hub.core.wincar_thumbs_index import (
    ThumbsIndexError,
    aggiorna_indice_thumbs,
    rimuovi_frame,
)


def _jpeg(color: tuple[int, int, int], size: tuple[int, int] = (49, 88)) -> bytes:
    """JPEG con subsampling=1 (fattori 2,1): deve combaciare col tag
    YCbCrSubSampling hardcoded nel modulo sotto test, altrimenti il decoder
    TIFF fallisce sul frame anche se la struttura del file è corretta."""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=85, subsampling=1)
    return buffer.getvalue()


def test_crea_file_nuovo_se_non_esiste(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    assert not path.exists()

    aggiorna_indice_thumbs(
        path, nome_thumb="foto1.jpg.thumb", jpeg=_jpeg((200, 0, 0)), width=49, height=88
    )

    assert path.exists()
    with Image.open(path) as im:
        assert im.format == "TIFF"
        assert im.size == (49, 88)
        assert im.tag_v2.get(270) == "foto1.jpg.thumb"


def test_append_multiplo_produce_un_frame_per_foto(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    colori = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for i, colore in enumerate(colori):
        aggiorna_indice_thumbs(
            path, nome_thumb=f"foto{i}.jpg.thumb", jpeg=_jpeg(colore), width=49, height=88
        )

    with Image.open(path) as im:
        n_frame = 0
        while True:
            try:
                im.seek(n_frame)
                n_frame += 1
            except EOFError:
                break
        assert n_frame == 3

        for i in range(3):
            im.seek(i)
            assert im.tag_v2.get(270) == f"foto{i}.jpg.thumb"
            # decodifica i pixel: verifica che il JPEG incapsulato sia
            # davvero leggibile, non solo che la entry IFD esista.
            im.convert("RGB").load()


def test_append_non_modifica_i_byte_dei_frame_preesistenti(tmp_path: Path) -> None:
    """La garanzia di sicurezza centrale: dopo un append, ogni singolo byte
    del contenuto preesistente deve restare identico — solo il puntatore
    "next IFD" dell'ultimo frame (4 byte) può cambiare, il resto è append-only."""
    path = tmp_path / "Thumbs.thumb"
    aggiorna_indice_thumbs(
        path, nome_thumb="foto0.jpg.thumb", jpeg=_jpeg((10, 20, 30)), width=49, height=88
    )
    aggiorna_indice_thumbs(
        path, nome_thumb="foto1.jpg.thumb", jpeg=_jpeg((40, 50, 60)), width=49, height=88
    )
    prima = path.read_bytes()

    aggiorna_indice_thumbs(
        path, nome_thumb="foto2.jpg.thumb", jpeg=_jpeg((70, 80, 90)), width=49, height=88
    )
    dopo = path.read_bytes()

    assert len(dopo) > len(prima)
    diffs = [i for i in range(len(prima)) if prima[i] != dopo[i]]
    # Al massimo i 4 byte del campo "next IFD" dell'ultimo frame esistente
    # (potrebbero risultare meno di 4 byte diversi se alcuni byte del nuovo
    # valore coincidono per caso con quelli vecchi, ma mai byte AL DI FUORI
    # di un singolo campo a 4 byte contiguo).
    assert len(diffs) <= 4
    if diffs:
        assert max(diffs) - min(diffs) < 4


def test_frame_nuovo_leggibile_con_pixel_corretti(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    aggiorna_indice_thumbs(
        path, nome_thumb="foto0.jpg.thumb", jpeg=_jpeg((10, 20, 30)), width=49, height=88
    )
    aggiorna_indice_thumbs(
        path, nome_thumb="foto1.jpg.thumb", jpeg=_jpeg((250, 5, 5)), width=60, height=100
    )

    with Image.open(path) as im:
        im.seek(1)
        assert im.size == (60, 100)
        r, g, b = im.convert("RGB").getpixel((5, 5))
        assert r > 200 and g < 30 and b < 30


def test_header_non_tiff_solleva_invece_di_appendere(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    path.write_bytes(b"non e' affatto un TIFF")

    with pytest.raises(ThumbsIndexError):
        aggiorna_indice_thumbs(
            path, nome_thumb="foto.jpg.thumb", jpeg=_jpeg((1, 2, 3)), width=49, height=88
        )


def _crea_indice(path: Path, nomi: list[str]) -> None:
    for i, nome in enumerate(nomi):
        aggiorna_indice_thumbs(
            path, nome_thumb=nome, jpeg=_jpeg((i * 10, i * 20, i * 30)), width=49, height=88
        )


def _frame_descrizioni(path: Path) -> list[str]:
    with Image.open(path) as im:
        out = []
        i = 0
        while True:
            try:
                im.seek(i)
            except EOFError:
                break
            out.append(im.tag_v2.get(270))
            i += 1
        return out


def test_rimuovi_frame_su_file_inesistente_e_no_op(tmp_path: Path) -> None:
    assert rimuovi_frame(tmp_path / "Thumbs.thumb", nome_thumb="foto.jpg.thumb") is False


def test_rimuovi_frame_descrizione_assente_e_no_op(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb"])
    assert rimuovi_frame(path, nome_thumb="non-esiste.jpg.thumb") is False
    assert _frame_descrizioni(path) == ["foto0.jpg.thumb"]


def test_rimuovi_unico_frame_cancella_il_file(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb"])
    assert rimuovi_frame(path, nome_thumb="foto0.jpg.thumb") is True
    assert not path.exists()


def test_rimuovi_primo_frame_di_tre(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb", "foto1.jpg.thumb", "foto2.jpg.thumb"])
    assert rimuovi_frame(path, nome_thumb="foto0.jpg.thumb") is True
    assert _frame_descrizioni(path) == ["foto1.jpg.thumb", "foto2.jpg.thumb"]
    with Image.open(path) as im:
        im.seek(0)
        im.convert("RGB").load()  # ancora decodificabile


def test_rimuovi_frame_centrale_di_tre(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb", "foto1.jpg.thumb", "foto2.jpg.thumb"])
    assert rimuovi_frame(path, nome_thumb="foto1.jpg.thumb") is True
    assert _frame_descrizioni(path) == ["foto0.jpg.thumb", "foto2.jpg.thumb"]


def test_rimuovi_ultimo_frame_di_tre(tmp_path: Path) -> None:
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb", "foto1.jpg.thumb", "foto2.jpg.thumb"])
    assert rimuovi_frame(path, nome_thumb="foto2.jpg.thumb") is True
    assert _frame_descrizioni(path) == ["foto0.jpg.thumb", "foto1.jpg.thumb"]


def test_rimuovi_frame_non_tocca_i_byte_dei_frame_rimanenti(tmp_path: Path) -> None:
    """Stessa garanzia dell'append, in direzione opposta: i byte dei frame
    NON rimossi devono restare identici — la rimozione patcha solo un
    puntatore (header o next-IFD del frame precedente), mai ricodifica."""
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb", "foto1.jpg.thumb", "foto2.jpg.thumb"])

    # Offset assoluto del frame1 (quello che sopravvive) prima della rimozione.
    from lys_workflow_hub.core.wincar_thumbs_index import _walk_ifd_chain_full

    prima_bytes = path.read_bytes()
    frames_prima = _walk_ifd_chain_full(prima_bytes)
    frame1_offset = frames_prima[1]["ifd_offset"]
    frame1_len = frames_prima[1]["next_field_offset"] + 4 - frame1_offset
    frame1_bytes_prima = prima_bytes[frame1_offset : frame1_offset + frame1_len]

    rimuovi_frame(path, nome_thumb="foto0.jpg.thumb")

    dopo_bytes = path.read_bytes()
    frame1_bytes_dopo = dopo_bytes[frame1_offset : frame1_offset + frame1_len]
    assert frame1_bytes_prima == frame1_bytes_dopo


def test_rimuovi_poi_appendi_ancora_funziona(tmp_path: Path) -> None:
    """La catena deve restare valida per operazioni successive dopo una
    rimozione (non solo leggibile una tantum)."""
    path = tmp_path / "Thumbs.thumb"
    _crea_indice(path, ["foto0.jpg.thumb", "foto1.jpg.thumb"])
    rimuovi_frame(path, nome_thumb="foto0.jpg.thumb")
    aggiorna_indice_thumbs(
        path, nome_thumb="foto2.jpg.thumb", jpeg=_jpeg((9, 9, 9)), width=49, height=88
    )
    assert _frame_descrizioni(path) == ["foto1.jpg.thumb", "foto2.jpg.thumb"]


def test_catena_ifd_ciclica_solleva_invece_di_girare_per_sempre(tmp_path: Path) -> None:
    """Un file con l'header giusto ma la catena IFD che punta a un offset
    già visitato (o comunque non crescente) deve fallire subito, non
    incastrare la richiesta in un loop infinito — vedi ThumbsIndexError."""
    import struct

    path = tmp_path / "Thumbs.thumb"
    # Header valido, first-IFD-offset=8, e a offset 8 un "IFD" fasullo con 0
    # entry il cui campo "next" punta di nuovo a 8 (ciclo di un solo nodo).
    data = b"II\x2a\x00" + struct.pack("<I", 8) + struct.pack("<H", 0) + struct.pack("<I", 8)
    path.write_bytes(data)

    with pytest.raises(ThumbsIndexError):
        aggiorna_indice_thumbs(
            path, nome_thumb="foto.jpg.thumb", jpeg=_jpeg((1, 2, 3)), width=49, height=88
        )
