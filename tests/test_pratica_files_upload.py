"""Test unitari per `save_upload` (upload foto/documenti dal portale esterno, v3.0 fase 6)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from lys_workflow_hub.core.pratica_files import UploadRifiutato, save_upload


def _jpeg_bytes(size: tuple[int, int] = (576, 1024)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(120, 40, 10)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _jpeg_bytes_ruotato(size: tuple[int, int], orientation: int) -> bytes:
    """JPEG con tag EXIF Orientation — simula una foto da smartphone dove il
    sensore scatta sempre "orizzontale" e la rotazione è solo metadato.
    `size` è la dimensione dei pixel grezzi (pre-rotazione)."""
    exif = Image.Exif()
    exif[0x0112] = orientation  # tag Orientation
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(10, 80, 200)).save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_save_upload_foto_salva_in_cartella_foto(tmp_path: Path) -> None:
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=b"fake-jpeg-bytes",
    )
    assert target.exists()
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Foto"
    assert target.read_bytes() == b"fake-jpeg-bytes"
    assert target.name.startswith("danno_")
    assert target.suffix == ".jpg"


def test_save_upload_documento_salva_in_cartella_allegati(tmp_path: Path) -> None:
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="documento",
        filename="preventivo.pdf",
        raw=b"%PDF-1.4 fake",
    )
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Allegati"
    assert target.name.startswith("preventivo_")


def test_save_upload_foto_rifiuta_estensione_non_immagine(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="virus.exe",
            raw=b"MZ-fake",
        )


def test_save_upload_documento_rifiuta_estensione_pericolosa(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="documento",
            filename="script.exe",
            raw=b"MZ-fake",
        )


def test_save_upload_rifiuta_file_vuoto(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="vuoto.jpg",
            raw=b"",
        )


def test_save_upload_rifiuta_file_troppo_grande(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="grande.jpg",
            raw=b"x" * 100,
            max_bytes=50,
        )


def test_save_upload_sanifica_path_traversal(tmp_path: Path) -> None:
    """Un nome file con componenti di percorso non deve scrivere fuori dalla
    cartella della pratica — solo il basename viene usato."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="../../etc/danno.jpg",
        raw=b"fake",
    )
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Foto"
    assert ".." not in target.parts


def test_save_upload_non_sovrascrive_file_omonimo(tmp_path: Path) -> None:
    """Due upload con lo stesso nome file originale non devono mai collidere
    (niente sovrascrittura silenziosa di foto/documenti già archiviati)."""
    t1 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno.jpg", raw=b"primo",
    )
    t2 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno.jpg", raw=b"secondo",
    )
    assert t1 != t2
    assert t1.read_bytes() == b"primo"
    assert t2.read_bytes() == b"secondo"


def test_save_upload_categoria_non_valida(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="qualcosa_altro",
            filename="file.pdf",
            raw=b"dati",
        )


def test_save_upload_foto_genera_thumb_wincar(tmp_path: Path) -> None:
    """WinCar affianca a ogni foto un file <nome>.thumb (JPEG in miniatura,
    lato lungo 88px) — senza non mostra la foto nella sua UI. Verifica che
    save_upload lo generi accanto al file originale, con le dimensioni
    corrette (aspect ratio preservato, lato lungo scalato a 88px)."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=_jpeg_bytes((576, 1024)),
    )
    thumb_path = target.with_name(target.name + ".thumb")
    assert thumb_path.exists()

    with Image.open(thumb_path) as thumb:
        assert thumb.format == "JPEG"
        w, h = thumb.size
        # Lato lungo (altezza, originale 1024) scalato a 88px, lato corto
        # in proporzione — PIL arrotonda per difetto qui (49, non 50).
        assert h == 88
        assert w == 49


def test_save_upload_foto_crea_thumbs_thumb(tmp_path: Path) -> None:
    """Il solo sidecar <nome>.jpg.thumb non basta a far comparire la foto in
    WinCar (verificato manualmente) — serve anche l'indice condiviso
    Thumbs.thumb nella stessa cartella."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=_jpeg_bytes((576, 1024)),
    )
    indice = target.parent / "Thumbs.thumb"
    assert indice.exists()
    with Image.open(indice) as im:
        assert im.tag_v2.get(270) == target.name + ".thumb"


def test_save_upload_due_foto_thumbs_thumb_ha_due_frame(tmp_path: Path) -> None:
    t1 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno1.jpg", raw=_jpeg_bytes(),
    )
    t2 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno2.jpg", raw=_jpeg_bytes(),
    )
    with Image.open(t1.parent / "Thumbs.thumb") as im:
        n = 0
        while True:
            try:
                im.seek(n)
                n += 1
            except EOFError:
                break
        assert n == 2
        im.seek(0)
        assert im.tag_v2.get(270) == t1.name + ".thumb"
        im.seek(1)
        assert im.tag_v2.get(270) == t2.name + ".thumb"


def test_save_upload_documento_non_genera_thumb(tmp_path: Path) -> None:
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="documento",
        filename="preventivo.pdf",
        raw=b"%PDF-1.4 fake",
    )
    assert not target.with_name(target.name + ".thumb").exists()
    assert not (target.parent / "Thumbs.thumb").exists()


def test_save_upload_foto_illeggibile_non_genera_thumb_ma_salva_comunque(
    tmp_path: Path,
) -> None:
    """Un'immagine che Pillow non riesce a decodificare (es. HEIC senza
    plugin, o dati corrotti) non deve bloccare l'upload della foto vera e
    propria — solo saltare la generazione del thumb, con un warning nel log."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=b"non-sono-davvero-byte-jpeg",
    )
    assert target.exists()
    assert not target.with_name(target.name + ".thumb").exists()


def test_scan_ignora_i_file_thumb(tmp_path: Path) -> None:
    """I file .thumb generati non devono comparire come foto/documenti
    separati nella scansione della pratica (già filtrati da _is_ignored,
    verifica di non regressione con un thumb generato realmente)."""
    from lys_workflow_hub.core.pratica_files import scan

    save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=_jpeg_bytes(),
    )
    allegati = scan(tmp_path, 766)
    nomi = [a.nome_file for a in allegati.tutti]
    assert len(nomi) == 1
    assert not any(n.endswith(".thumb") for n in nomi)


def test_save_upload_foto_ruotata_applica_orientamento_exif(tmp_path: Path) -> None:
    """Regressione: senza `ImageOps.exif_transpose()` il thumb usciva con le
    proporzioni scambiate (es. 88x49 invece di 49x88) per una foto scattata
    in verticale ma con pixel grezzi orizzontali + tag EXIF di rotazione —
    il caso comune delle foto da smartphone."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        # Pixel grezzi 1024x576 (orizzontali) + Orientation=6 (ruota 90° CW
        # in visualizzazione) -> risultato logico verticale 576x1024, come
        # la foto "dritta" di test_save_upload_foto_genera_thumb_wincar.
        raw=_jpeg_bytes_ruotato((1024, 576), orientation=6),
    )
    thumb_path = target.with_name(target.name + ".thumb")
    assert thumb_path.exists()

    with Image.open(thumb_path) as thumb:
        w, h = thumb.size
        assert (w, h) == (49, 88)


def test_save_upload_foto_thumb_scrittura_fallita_non_blocca_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se la scrittura del file .thumb fallisce (es. permessi, disco pieno),
    l'upload della foto vera e propria deve comunque andare a buon fine."""

    def _write_bytes_fallisce(self: Path, data: bytes) -> int:
        raise OSError("disco pieno (simulato)")

    monkeypatch.setattr(Path, "write_bytes", _write_bytes_fallisce)

    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=_jpeg_bytes(),
    )
    assert target.exists()
    assert target.read_bytes() != b""


def test_save_upload_thumbs_index_fallito_non_blocca_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se l'aggiornamento di Thumbs.thumb fallisce (es. formato inatteso,
    WinCar ha il file aperto su Windows), la foto e il suo sidecar .thumb
    devono comunque salvarsi correttamente."""
    import lys_workflow_hub.core.pratica_files as pratica_files_mod

    def _fallisce(*args, **kwargs):
        raise RuntimeError("Thumbs.thumb non aggiornabile (simulato)")

    monkeypatch.setattr(pratica_files_mod, "aggiorna_indice_thumbs", _fallisce)

    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=_jpeg_bytes(),
    )
    assert target.exists()
    assert target.with_name(target.name + ".thumb").exists()
    assert not (target.parent / "Thumbs.thumb").exists()
