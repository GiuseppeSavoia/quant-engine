"""
File Excel (.xlsx) scritti a mano, senza librerie: un .xlsx è uno zip di file XML (Office Open XML).

Quel che serve per un modello leggibile in Excel, Numbers e Anteprima: celle con numeri, testo o formule (con il valore
già calcolato, così chi non ricalcola vede comunque i numeri giusti), pochi stili (grassetto, input in blu come nei
modelli delle banche, percentuali, milioni, decimali), larghezza delle colonne, riquadri bloccati. Le formule si
scrivono in inglese con il punto decimale (es. "=SUM(B2:B5)*0.5"): Excel le mostra tradotte nella lingua dell'utente.
`fullCalcOnLoad` fa ricalcolare tutto all'apertura.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# stili: nome -> (formato numerico, grassetto, colore del carattere o None)
STYLES = {
    "text": (None, False, None),
    "bold": (None, True, None),
    "title": (None, True, None),
    "note": (None, False, "FF6E6E6E"),
    "int": ("#,##0", False, None),
    "num": ("#,##0.00", False, None),
    "num1": ("#,##0.0", False, None),
    "mln": ("#,##0.0,,", False, None),       # valori in unità, mostrati in milioni
    "pct": ("0.0%", False, None),
    "mult": ('0.0"x"', False, None),
    "date": ("dd/mm/yyyy", False, None),
    "in_pct": ("0.0%", False, "FF0000FF"),   # input (leve) in blu, formule in nero
    "in_mln": ("#,##0.0,,", False, "FF0000FF"),
    "in_num": ("#,##0.00", False, "FF0000FF"),
    "in_int": ("#,##0", False, "FF0000FF"),
    "out_num": ("#,##0.00", True, None),
    "out_pct": ("0.0%", True, None),
    "out_mln": ("#,##0.0,,", True, None),
}
_FORMULA_ITALIAN = re.compile(r"\b(SOMMA|SE|MEDIA|ARROTONDA|CERCA\.VERT|MIN\.|MAX\.)\(", re.I)


@dataclass
class Cell:
    value: float | str | None = None
    formula: str | None = None       # senza "=" iniziale o con: si toglie
    style: str = "text"


@dataclass
class Sheet:
    name: str
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)   # (riga, colonna) da 1
    widths: dict[int, float] = field(default_factory=dict)
    freeze: tuple[int, int] | None = None                               # (righe, colonne) bloccate

    def set(self, row: int, col: int, value=None, style: str = "text", formula: str | None = None) -> None:
        if formula is not None:
            f = formula[1:] if formula.startswith("=") else formula
            if _FORMULA_ITALIAN.search(f) or ";" in f:  # nomi italiani o separatore ";" = formula non in inglese
                raise ValueError(f"formula non in inglese: {formula}")
            formula = f
        self.cells[(row, col)] = Cell(value, formula, style)

    def row(self, row: int, col: int, values: list, style: str = "text") -> None:
        for i, v in enumerate(values):
            self.set(row, col + i, v, style)


def col_letter(col: int) -> str:
    s = ""
    while col:
        col, r = divmod(col - 1, 26)
        s = chr(65 + r) + s
    return s


def ref(row: int, col: int, absolute: bool = False) -> str:
    return f"${col_letter(col)}${row}" if absolute else f"{col_letter(col)}{row}"


def _number(v) -> str | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return repr(x) if math.isfinite(x) else None


def _cell_xml(r: int, c: int, cell: Cell, style_ids: dict[str, int]) -> str:
    a = f'r="{ref(r, c)}" s="{style_ids.get(cell.style, 0)}"'
    if cell.formula is not None:
        num = _number(cell.value)
        if num is not None:
            return f"<c {a}><f>{escape(cell.formula)}</f><v>{num}</v></c>"
        if isinstance(cell.value, str):
            return f'<c {a} t="str"><f>{escape(cell.formula)}</f><v>{escape(cell.value)}</v></c>'
        return f"<c {a}><f>{escape(cell.formula)}</f></c>"
    if cell.value is None:
        return f"<c {a}/>"
    if isinstance(cell.value, (bool,)):
        return f'<c {a} t="b"><v>{int(cell.value)}</v></c>'
    num = _number(cell.value) if not isinstance(cell.value, str) else None
    if num is not None:
        return f"<c {a}><v>{num}</v></c>"
    if not isinstance(cell.value, str):  # NaN / inf: cella vuota
        return f"<c {a}/>"
    text = escape(cell.value.replace("\x00", ""))
    return f'<c {a} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(sheet: Sheet, style_ids: dict[str, int]) -> str:
    rows: dict[int, list] = {}
    for (r, c), cell in sheet.cells.items():
        rows.setdefault(r, []).append((c, cell))
    body = "".join(f'<row r="{r}">' + "".join(_cell_xml(r, c, cell, style_ids) for c, cell in sorted(cells,
                                                                                                    key=lambda x: x[0]))
                   + "</row>" for r, cells in sorted(rows.items()))
    views = '<sheetViews><sheetView workbookViewId="0"'
    if sheet.freeze:
        fr, fc = sheet.freeze
        top = ref(fr + 1, fc + 1)
        pane = "bottomRight" if fr and fc else ("bottomLeft" if fr else "topRight")
        views += (f'><pane xSplit="{fc}" ySplit="{fr}" topLeftCell="{top}" activePane="{pane}" state="frozen"/>'
                  "</sheetView></sheetViews>")
    else:
        views += "/></sheetViews>"
    cols = ""
    if sheet.widths:
        cols = "<cols>" + "".join(f'<col min="{c}" max="{c}" width="{w}" customWidth="1"/>'
                                  for c, w in sorted(sheet.widths.items())) + "</cols>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{views}{cols}<sheetData>{body}</sheetData></worksheet>")


def _styles_xml() -> tuple[str, dict[str, int]]:
    fmts, fonts, xfs, ids = {}, ["", "b"], [], {}
    colors = []
    for name, (fmt, bold, color) in STYLES.items():
        fid = 0
        if fmt is not None:
            fid = fmts.setdefault(fmt, 164 + len(fmts))
        if color:
            if (bold, color) not in colors:
                colors.append((bold, color))
            font = 2 + colors.index((bold, color))
        else:
            font = 1 if bold else 0
        ids[name] = len(xfs)
        xfs.append(f'<xf numFmtId="{fid}" fontId="{font}" fillId="0" borderId="0" xfId="0"'
                   + (' applyNumberFormat="1"' if fid else "") + (' applyFont="1"' if font else "") + "/>")
    font_xml = ['<font><sz val="11"/><name val="Calibri"/></font>', '<font><b/><sz val="11"/><name val="Calibri"/></font>']
    font_xml += [f'<font>{"<b/>" if b else ""}<sz val="11"/><color rgb="{c}"/><name val="Calibri"/></font>'
                 for b, c in colors]
    num_xml = "".join(f'<numFmt numFmtId="{i}" formatCode="{escape(f, {chr(34): "&quot;"})}"/>'
                      for f, i in fmts.items())
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           f'<numFmts count="{len(fmts)}">{num_xml}</numFmts>'
           f'<fonts count="{len(font_xml)}">{"".join(font_xml)}</fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/>'
           '</fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    return xml, ids


def workbook_bytes(sheets: list[Sheet]) -> bytes:
    """Il file .xlsx con i fogli nell'ordine dato."""
    styles, ids = _styles_xml()
    n = len(sheets)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, n + 1)) + "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    names = []
    for s in sheets:  # nomi dei fogli: max 31 caratteri, niente []:*?/\
        name = re.sub(r"[\[\]:*?/\\]", " ", s.name)[:31] or "Foglio"
        names.append(name)
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{escape(nm, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                    for i, nm in enumerate(names, 1))
          + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
               + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/styles" Target="styles.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        for i, s in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(s, ids))
    return buf.getvalue()
