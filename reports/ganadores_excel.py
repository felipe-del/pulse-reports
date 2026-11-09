"""
reports/ganadores_excel.py
Genera el Excel de ganadores: Resumen General + una hoja por campaña.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from queries.ganadores import WinnersData, sheet_name

DATE_FMT = "dd/mm/yyyy hh:mm:ss"
HEADER_ROW = 4          # en hojas de campaña (título, totales, en blanco, encabezado)
SUMMARY_HEADER_ROW = 5

_FILL_HEADER = PatternFill("solid", fgColor="1F3864")
_FONT_HEADER = Font(bold=True, color="FFFFFF")
_FILL_TOTAL = PatternFill("solid", fgColor="D9E1F2")
_FONT_TITLE = Font(bold=True, size=14, color="1F3864")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# (encabezado, clave, ancho, tipo)  tipo: int | text | datetime
DETAIL_COLUMNS = [
    ("ID registro",        "registro_ganador_id", 12, "int"),
    ("ID cliente",         "cliente_id",          12, "int"),
    ("Nombre completo",    "nombre_completo",     34, "text"),
    ("Identificación",     "identificacion",      20, "text"),
    ("Correo electrónico", "correo_electronico",  34, "text"),
    ("Teléfono",           "telefono",            16, "text"),
    ("País",               "pais",                14, "text"),
    ("ID campaña",         "campania_id",         12, "int"),
    ("Campaña",            "nombre_campania",     34, "text"),
    ("Tipo de premio",     "tipo_premio",         16, "text"),
    ("Cantidad de premios", "cantidad_premios",   12, "int"),
    ("Estado (código)",    "estado",              12, "int"),
    ("Fecha y hora ganó",  "fecha_hora_ganador",  20, "datetime"),
    ("Última actualización", "fecha_actualizacion", 20, "datetime"),
    ("ID comercio",        "comercio_id",         12, "int"),
    ("ID tienda (compra)", "tienda_id",           14, "int"),
    ("Tienda (compra)",    "nombre_tienda",       30, "text"),
    ("Nombre público tienda", "nombre_publico_tienda", 30, "text"),
    ("ID transacción venta", "transaccion_venta_id", 16, "int"),
    ("Factura POS",        "factura_pos",         20, "text"),
    ("Fecha y hora compra", "fecha_venta",        20, "datetime"),
]


def _py(v):
    """NaN/NaT/numpy -> valores nativos de Python aptos para openpyxl."""
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):
        return v.item()
    return v


def _put(ws, row: int, col: int, value, kind: str = "text", fmt: str | None = None):
    cell = ws.cell(row=row, column=col)
    value = _py(value)
    if kind == "int" and value is not None and not isinstance(value, (int, float)):
        kind = "text"
    cell.value = value
    if kind == "text" and isinstance(value, str):
        cell.data_type = "s"        # evita que "=..." se interprete como fórmula
        cell.number_format = "@"
    elif kind == "int":
        cell.number_format = fmt or "0"
    elif kind == "num":
        cell.number_format = fmt or "0.00"
    elif kind == "datetime":
        cell.number_format = DATE_FMT
    cell.border = _BORDER
    return cell


def _style_header(ws, row: int, ncols: int):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill, cell.font, cell.border = _FILL_HEADER, _FONT_HEADER, _BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _campaign_sheet(wb: Workbook, camp: dict, g: pd.DataFrame, data: WinnersData):
    ws = wb.create_sheet(sheet_name(camp))
    ncols = len(DETAIL_COLUMNS)
    name = data.campaign_names.get(camp["campaign_id"]) or "(sin nombre)"

    ws["A1"] = f"{camp['pais']} — {camp['premio']} — Campaña {camp['campaign_id']} · {name}"
    ws["A1"].font = _FONT_TITLE

    first, last = HEADER_ROW + 1, HEADER_ROW + len(g)
    rng_last = max(last, first)  # rango válido aunque no haya ganadores
    col_ix = {k: i + 1 for i, (_, k, _, _) in enumerate(DETAIL_COLUMNS)}
    L = {k: get_column_letter(i) for k, i in col_ix.items()}

    # Totales (SUBTOTAL respeta los autofiltros)
    ws["A2"] = "Registros (visibles):"
    ws["C2"] = f"=SUBTOTAL(103,{L['registro_ganador_id']}{first}:{L['registro_ganador_id']}{rng_last})"
    ws["D2"] = "Clientes únicos:"
    ws["E2"] = len(g["cliente_id"].dropna().unique())
    ws["K1"] = "Total premios:"
    ws["K2"] = f"=SUBTOTAL(109,{L['cantidad_premios']}{first}:{L['cantidad_premios']}{rng_last})"
    for ref in ("A2", "D2", "K1"):
        ws[ref].font = Font(bold=True)
    for ref in ("C2", "E2", "K2"):
        ws[ref].font = Font(bold=True)
        ws[ref].number_format = "#,##0"
        ws[ref].fill = _FILL_TOTAL
    ws["F2"] = "(único sobre toda la hoja)"
    ws["F2"].font = Font(italic=True, color="7F7F7F", size=9)

    for i, (head, _, width, _) in enumerate(DETAIL_COLUMNS, start=1):
        ws.cell(row=HEADER_ROW, column=i, value=head)
        ws.column_dimensions[get_column_letter(i)].width = width
    _style_header(ws, HEADER_ROW, ncols)

    for r, rec in enumerate(g.itertuples(index=False), start=first):
        d = rec._asdict()
        for i, (_, key, _, kind) in enumerate(DETAIL_COLUMNS, start=1):
            _put(ws, r, i, d[key], kind)

    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(ncols)}{max(last, HEADER_ROW)}"
    ws.freeze_panes = f"A{HEADER_ROW + 1}"
    return ws


def _country_table(ws, summary: pd.DataFrame, first: int, last: int, title_row: int) -> int:
    """Consolidado por país, con valores calculados desde `summary` (sin fórmulas,
    para que se vean en cualquier visor aunque no recalcule).
    Devuelve la primera fila libre después de la tabla."""
    ws.cell(row=title_row, column=1,
            value="RESUMEN DE PREMIOS GANADOS POR PAÍS").font = _FONT_TITLE
    hr = title_row + 1
    ws.cell(row=hr, column=1, value="País")
    ws.cell(row=hr, column=2, value="Campañas incluidas")
    ws.cell(row=hr, column=5, value="Total de ganadores")
    ws.cell(row=hr, column=6, value="Total de premios ganados")
    ws.merge_cells(start_row=hr, start_column=2, end_row=hr, end_column=4)
    _style_header(ws, hr, 6)

    paises = list(dict.fromkeys(summary["pais"]))   # conserva el orden del catálogo
    r0 = hr + 1
    for r, pais in enumerate(paises, start=r0):
        ids = ", ".join(str(i) for i in summary.loc[summary["pais"] == pais, "campania_id"])
        _put(ws, r, 1, pais)
        _put(ws, r, 2, ids)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        for c in (3, 4):
            ws.cell(row=r, column=c).border = _BORDER
        grp = summary[summary["pais"] == pais]
        _put(ws, r, 5, int(grp["registros"].sum()), "int", "#,##0")
        _put(ws, r, 6, int(grp["total_premios"].sum()), "int", "#,##0")
    r1 = r0 + len(paises) - 1

    tr = r1 + 1
    ws.cell(row=tr, column=1, value="TOTAL GENERAL")
    ws.cell(row=tr, column=2, value="Todas")
    ws.merge_cells(start_row=tr, start_column=2, end_row=tr, end_column=4)
    ws.cell(row=tr, column=5, value=int(summary["registros"].sum()))
    ws.cell(row=tr, column=6, value=int(summary["total_premios"].sum()))
    for c in range(1, 7):
        cell = ws.cell(row=tr, column=c)
        cell.fill, cell.font, cell.border = _FILL_TOTAL, Font(bold=True), _BORDER
        if c in (5, 6):
            cell.number_format = "#,##0"
    return tr + 2


def _summary_sheet(wb: Workbook, data: WinnersData, summary: pd.DataFrame,
                   generated_at: datetime):
    ws = wb.active
    ws.title = "Resumen General"
    heads = [
        ("País", 16), ("ID campaña", 12), ("Campaña", 38), ("Tipo de premio", 16),
        ("Registros ganadores", 14), ("Clientes únicos", 14),
        ("Suma totalPrizes", 14), ("Primer ganador", 20), ("Último ganador", 20),
        ("Estado de los registros (código: cantidad)", 40),
    ]
    ws["A1"] = "Ganadores — Campañas Gane Premios · Grupo UNO"
    ws["A1"].font = _FONT_TITLE
    ws["A2"] = (f"Generado: {generated_at:%d/%m/%Y %H:%M:%S} · "
                f"Fechas en hora local ({data.flt.tz}), sin conversión adicional")
    ws["A3"] = f"Filtros: {data.flt.describe()}"
    for ref in ("A2", "A3"):
        ws[ref].font = Font(italic=True, color="595959")

    for i, (h, w) in enumerate(heads, start=1):
        ws.cell(row=SUMMARY_HEADER_ROW, column=i, value=h)
        ws.column_dimensions[get_column_letter(i)].width = w
    _style_header(ws, SUMMARY_HEADER_ROW, len(heads))

    keys = [("pais", "text"), ("campania_id", "int"), ("nombre_campania", "text"),
            ("tipo_premio", "text"), ("registros", "int"), ("clientes_unicos", "int"),
            ("total_premios", "int"), ("primer_ganador", "datetime"),
            ("ultimo_ganador", "datetime"), ("estados", "text")]
    first = SUMMARY_HEADER_ROW + 1
    for r, rec in enumerate(summary.to_dict("records"), start=first):
        for i, (k, kind) in enumerate(keys, start=1):
            fmt = "#,##0" if k in ("registros", "clientes_unicos", "total_premios") else None
            _put(ws, r, i, rec[k], kind, fmt)
    last = first + len(summary) - 1

    tr = last + 1
    ws.cell(row=tr, column=1, value="TOTAL GENERAL")
    ws.cell(row=tr, column=5, value=int(summary["registros"].sum()))
    ws.cell(row=tr, column=6, value=int(data.detail["cliente_id"].nunique()))
    ws.cell(row=tr, column=7, value=int(summary["total_premios"].sum()))
    ws.cell(row=tr, column=8, value=_py(summary["primer_ganador"].min()))
    ws.cell(row=tr, column=9, value=_py(summary["ultimo_ganador"].max()))
    for c in range(1, len(heads) + 1):
        cell = ws.cell(row=tr, column=c)
        cell.fill, cell.font, cell.border = _FILL_TOTAL, Font(bold=True), _BORDER
        if c in (5, 6, 7):
            cell.number_format = "#,##0"
        if c in (8, 9):
            cell.number_format = DATE_FMT
    note_row = _country_table(ws, summary, first, last, tr + 2)

    note_font = Font(italic=True, color="7F7F7F", size=9)
    ws.cell(row=note_row, column=1,
            value="* Clientes únicos del total = clientes distintos entre todas las campañas "
                  "(no es la suma de la columna, un cliente puede ganar en más de una).").font = note_font
    ws.cell(row=note_row + 1, column=1,
            value="* Premios ganados = registros en CustomerPrizeWins; no implica entrega física. "
                  "El código de estado se muestra tal cual está en BD.").font = note_font

    ws.auto_filter.ref = f"A{SUMMARY_HEADER_ROW}:{get_column_letter(len(heads))}{last}"
    ws.freeze_panes = f"A{SUMMARY_HEADER_ROW + 1}"
    return ws


def write_workbook(path: str, data: WinnersData, summary: pd.DataFrame,
                   generated_at: datetime | None = None) -> str:
    """Las validaciones internas (ganadores_validation) no se escriben en el Excel."""
    generated_at = generated_at or datetime.now()
    wb = Workbook()
    _summary_sheet(wb, data, summary, generated_at)
    for camp in data.catalog:
        g = data.detail[data.detail["campania_id"] == camp["campaign_id"]]
        _campaign_sheet(wb, camp, g, data)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    return path
