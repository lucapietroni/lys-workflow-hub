"""Generatore DOCX per verbali di consegna/riconsegna veicolo di cortesia."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from lys_workflow_hub.workflows.verbale_cortesia.data import (
    TIPO_USCITA,
    VerbaleData,
)
from lys_workflow_hub.workflows.cessione_credito.data import (
    CARROZZERIA_NOME,
    CARROZZERIA_VIA,
    CARROZZERIA_CAP,
    CARROZZERIA_COMUNE,
    CARROZZERIA_PIVA,
)


_ASSETS_DIR = Path(__file__).parent / "assets"
_LOGO_PNG = _ASSETS_DIR / "logo_lys.png"
_TIMBRO_PNG = _ASSETS_DIR / "timbro_lys.png"

COLOR_BLACK = RGBColor(0x00, 0x00, 0x00)
COLOR_GREY = RGBColor(0x4A, 0x55, 0x68)
COLOR_SECTION_BG = "F0F0F0"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _set_page_margins(doc: Document) -> None:
    for section in doc.sections:
        section.top_margin = Cm(1.2)
        section.bottom_margin = Cm(1.2)
        section.left_margin = Cm(1.6)
        section.right_margin = Cm(1.6)


def _run(para, text: str, *, bold: bool = False, size: float | None = None,
         color: RGBColor | None = None, italic: bool = False) -> None:
    r = para.add_run(text)
    if bold:
        r.bold = True
    if italic:
        r.italic = True
    if size is not None:
        r.font.size = Pt(size)
    if color is not None:
        r.font.color.rgb = color


def _set_cell_shading(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _set_cell_margins(cell, top: int = 30, bottom: int = 30,
                      left: int = 70, right: int = 70) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for side, val in [("top", top), ("bottom", bottom),
                      ("left", left), ("right", right)]:
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        tcMar.append(el)
    tc_pr.append(tcMar)


def _para(doc: Document, *, alignment=WD_ALIGN_PARAGRAPH.LEFT,
          space_before: float = 0, space_after: float = 2) -> object:
    p = doc.add_paragraph()
    p.alignment = alignment
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    return p


def _section_header(doc: Document, text: str) -> None:
    p = _para(doc, space_before=5, space_after=1)
    _run(p, text, bold=True, size=9)


def _lv(cell, label: str, value: str, label_size: float = 7.5,
        value_size: float = 8.5) -> None:
    """Write 'Label: Value' into a table cell with consistent styling."""
    _set_cell_margins(cell)
    para = cell.paragraphs[0]
    para.paragraph_format.space_before = Pt(1)
    para.paragraph_format.space_after = Pt(1)
    _run(para, f"{label}: ", bold=True, size=label_size, color=COLOR_GREY)
    _run(para, value or "", size=value_size)


def _merge_row_cols(table, row_idx: int, start_col: int, end_col: int) -> object:
    """Merge cells in a row from start_col to end_col (inclusive), return merged cell."""
    row = table.rows[row_idx]
    cell = row.cells[start_col]
    for i in range(start_col + 1, end_col + 1):
        cell = cell.merge(row.cells[i])
    return cell


def _set_row_height(table, row_idx: int, twips: int,
                    rule: str = "atLeast") -> None:
    tr_pr = table.rows[row_idx]._tr.get_or_add_trPr()
    trH = OxmlElement("w:trHeight")
    trH.set(qn("w:val"), str(twips))
    trH.set(qn("w:hRule"), rule)
    tr_pr.append(trH)


# ---------------------------------------------------------------------------
# Document sections
# ---------------------------------------------------------------------------


def _add_header(doc: Document, data: VerbaleData) -> None:
    if _LOGO_PNG.exists():
        p = _para(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
        p.add_run().add_picture(str(_LOGO_PNG), width=Cm(4.5))

    p = _para(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, space_before=1, space_after=1)
    _run(p, "VERBALE DI CONSEGNA VEICOLO DI CORTESIA"
         if data.tipo == TIPO_USCITA
         else "VERBALE DI RICONSEGNA VEICOLO DI CORTESIA",
         bold=True, size=12)

    p = _para(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, space_before=0, space_after=2)
    _run(p, f"(Verbale {data.label_tipo})", size=9, italic=True)

    p = _para(doc, alignment=WD_ALIGN_PARAGRAPH.RIGHT, space_after=4)
    _run(p, f"Pratica n° {data.numero_pratica}", size=8, color=COLOR_GREY)


def _add_locatario(doc: Document, data: VerbaleData) -> None:
    _section_header(doc, "Locatario")
    table = doc.add_table(rows=4, cols=4)
    table.style = "Table Grid"

    # Row 0: nome | codice fiscale
    c0 = _merge_row_cols(table, 0, 0, 1)
    _lv(c0, "Locatario", data.locatario_nome)
    c1 = _merge_row_cols(table, 0, 2, 3)
    _lv(c1, "Cod. Fiscale", data.codice_fiscale)

    # Row 1: indirizzo | localita | cap
    c0 = _merge_row_cols(table, 1, 0, 1)
    _lv(c0, "Indirizzo", data.indirizzo)
    c1 = table.rows[1].cells[2]
    _lv(c1, "Località", data.localita)
    c2 = table.rows[1].cells[3]
    _lv(c2, "CAP", data.cap)

    # Row 2: patente
    c0 = table.rows[2].cells[0]
    _lv(c0, "Patente N°", data.patente_numero)
    c1 = table.rows[2].cells[1]
    _lv(c1, "Rilasciata da", data.patente_rilasciata_da)
    c2 = table.rows[2].cells[2]
    _lv(c2, "il", data.patente_data_rilascio)
    c3 = table.rows[2].cells[3]
    _lv(c3, "Validità", data.patente_validita)

    # Row 3: telefono
    c0 = _merge_row_cols(table, 3, 0, 3)
    _lv(c0, "Telefono", data.telefono)


def _add_veicolo(doc: Document, data: VerbaleData) -> None:
    _section_header(doc, "Veicolo")
    table = doc.add_table(rows=4, cols=4)
    table.style = "Table Grid"

    # Row 0: marca/modello | telaio | targa
    c0 = _merge_row_cols(table, 0, 0, 1)
    _lv(c0, "Marca/Modello", data.marca_modello)
    c1 = table.rows[0].cells[2]
    _lv(c1, "Telaio", data.telaio)
    c2 = table.rows[0].cells[3]
    _lv(c2, "Targa", data.targa)

    # Row 1: km | carburante | omologato
    c0 = table.rows[1].cells[0]
    _lv(c0, data.label_km, data.km)
    c1 = table.rows[1].cells[1]
    _lv(c1, "Livello carburante", data.livello_carburante)
    c2 = _merge_row_cols(table, 1, 2, 3)
    _lv(c2, "Omologato per", data.omologato_per)

    # Row 2: max km mese | max km giorno | tariffa
    c0 = table.rows[2].cells[0]
    _lv(c0, "Max Km mese", data.max_km_mese)
    c1 = table.rows[2].cells[1]
    _lv(c1, "Max Km giorno", data.max_km_giorno)
    c2 = _merge_row_cols(table, 2, 2, 3)
    _lv(c2, "Tariffa Km eccedenti", data.tariffa_km_eccedenti)

    # Row 3: accessori (full width) — shorter height
    c0 = _merge_row_cols(table, 3, 0, 3)
    _lv(c0, "Accessori in dotazione", data.accessori)
    _set_row_height(table, 3, 400)


def _add_franchigie(doc: Document, data: VerbaleData) -> None:
    _section_header(doc, "Franchigie")
    table = doc.add_table(rows=2, cols=4)
    table.style = "Table Grid"

    c0 = table.rows[0].cells[0]
    _lv(c0, "RCA", f"{data.rca} €" if data.rca else "")
    c1 = table.rows[0].cells[1]
    _lv(c1, "KASCO", f"{data.kasco} €" if data.kasco else "")
    c2 = _merge_row_cols(table, 0, 2, 3)
    _lv(c2, "Furto Incendio", data.furto_incendio)

    c0 = _merge_row_cols(table, 1, 0, 3)
    _lv(c0, "Importo giornaliero della locazione €", data.importo_giornaliero)


def _add_danni(doc: Document, data: VerbaleData) -> None:
    label = (
        "Distinta danni vettura:"
        if data.tipo == TIPO_USCITA
        else "Distinta danni vettura alla riconsegna:"
    )
    _section_header(doc, label)

    rows_data = list(data.danni) + [("", "")] * max(0, 3 - len(data.danni))
    n_rows = len(rows_data) + 1  # +1 for header
    table = doc.add_table(rows=n_rows, cols=2)
    table.style = "Table Grid"

    for cell, text in zip(table.rows[0].cells, ["Parte danneggiata", "Dettaglio"]):
        _set_cell_shading(cell, COLOR_SECTION_BG)
        _set_cell_margins(cell)
        _run(cell.paragraphs[0], text, bold=True, size=8)

    for i, (parte, det) in enumerate(rows_data, start=1):
        _set_row_height(table, i, 300)
        for cell, val in zip(table.rows[i].cells, [parte, det]):
            _set_cell_margins(cell)
            if val:
                _run(cell.paragraphs[0], val, size=8.5)

    p = _para(doc, space_before=2, space_after=2)
    _run(
        p,
        "Il Locatario dichiara di aver accertato le condizioni generali del veicolo "
        "e di aver riscontrato/non riscontrato le sue perfette condizioni",
        italic=True, size=8,
    )


def _add_note(doc: Document, data: VerbaleData) -> None:
    _section_header(doc, "NOTE")
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    _set_cell_margins(cell, top=40, bottom=40)
    _set_row_height(table, 0, 600)
    if data.note:
        _run(cell.paragraphs[0], data.note, size=8.5)


def _add_firme(doc: Document, data: VerbaleData) -> None:
    p = _para(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, space_before=6, space_after=4)
    _run(p, f"Pratica n° {data.numero_pratica}", size=8.5, bold=True)

    # 3-column signature table: Data/ora | Il Locatario | Il Locatore
    table = doc.add_table(rows=2, cols=3)
    table.style = "Table Grid"

    # Row 0: headers
    labels = [f"Data e ora di {data.label_tipo}", "Il Locatario\nTimbro e firma", "Il Locatore\nTimbro e firma"]
    for cell, label in zip(table.rows[0].cells, labels):
        _set_cell_shading(cell, COLOR_SECTION_BG)
        _set_cell_margins(cell)
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for line in label.split("\n"):
            if line != label.split("\n")[0]:
                para.add_run("\n")
            _run(para, line, bold=(line == label.split("\n")[0]), size=8.5)

    # Row 1: data/ora + locatario blank + locatore timbro
    _set_row_height(table, 1, 1100)

    date_cell = table.rows[1].cells[0]
    _set_cell_margins(date_cell)
    date_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = date_cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, data.data_ora, size=9)

    # Locatario — linea firma
    loc_cell = table.rows[1].cells[1]
    _set_cell_margins(loc_cell)
    loc_cell.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
    p = loc_cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "........................", size=8.5)

    # Locatore — timbro + firma LYS Auto
    lys_cell = table.rows[1].cells[2]
    _set_cell_margins(lys_cell)
    lys_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = lys_cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if _TIMBRO_PNG.exists():
        p.add_run().add_picture(str(_TIMBRO_PNG), width=Cm(4.0))
    else:
        _run(p, "........................", size=8.5)


def _add_footer(doc: Document) -> None:
    section = doc.sections[0]
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(
        fp,
        f"{CARROZZERIA_NOME}  ·  {CARROZZERIA_VIA}, {CARROZZERIA_CAP} {CARROZZERIA_COMUNE}"
        f"  ·  P.IVA {CARROZZERIA_PIVA}",
        size=7, color=COLOR_GREY,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(data: VerbaleData, out: BinaryIO | None = None) -> bytes:
    """Build the verbale DOCX. Returns bytes; also writes to `out` if provided."""
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(9)

    _set_page_margins(doc)
    _add_header(doc, data)
    _add_locatario(doc, data)
    _add_veicolo(doc, data)

    if data.tipo == TIPO_USCITA:
        _add_franchigie(doc, data)

    _add_danni(doc, data)
    _add_note(doc, data)
    _add_firme(doc, data)
    _add_footer(doc)

    buf = BytesIO()
    doc.save(buf)
    raw = buf.getvalue()
    if out is not None:
        out.write(raw)
    return raw


def filename_for(data: VerbaleData) -> str:
    targa_safe = "".join(c for c in (data.targa or "XX") if c.isalnum())
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    return f"Verbale_{data.label_tipo}_{data.numero_pratica}_{targa_safe}_{today}.docx"
