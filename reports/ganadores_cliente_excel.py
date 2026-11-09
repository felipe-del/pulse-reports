"""
reports/ganadores_cliente_excel.py
Excel para el cliente Grupo UNO: Resumen General (solo consolidado por país)
+ una hoja por país con todos sus ganadores. Todos los totales son valores
calculados en Python (sin fórmulas). Reutiliza estilos y columnas del reporte interno.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from queries.ganadores import WinnersData
from reports.ganadores_excel import (
    _BORDER, _FILL_TOTAL, _FONT_TITLE, DETAIL_COLUMNS, _put, _style_header,
)
from reports.ganadores_validation import country_totals

HEADER_ROW = 4
SUMMARY_SHEET = "Resumen General"

# Columnas del detalle para el cliente, en el orden solicitado.
CLIENT_KEYS = [
    "registro_ganador_id", "cliente_id", "nombre_completo", "identificacion",
    "correo_electronico", "telefono", "campania_id", "nombre_campania",
    "fecha_hora_ganador",
    "tienda_id", "nombre_tienda", "nombre_publico_tienda", "transaccion_venta_id",
    "factura_pos", "fecha_venta",
]
_BY_KEY = {c[1]: c for c in DETAIL_COLUMNS}
CLIENT_COLUMNS = [_BY_KEY[k] for k in CLIENT_KEYS]


def countries(data: WinnersData) -> list[str]:
    return list(dict.fromkeys(c["pais"] for c in data.catalog))


def _summary_sheet(wb: Workbook, data: WinnersData, generated_at: datetime):
    ws = wb.active
    ws.title = SUMMARY_SHEET
    ws["A1"] = "Ganadores — Campañas Gane Premios · Grupo UNO"
    ws["A1"].font = _FONT_TITLE
    ws["A2"] = f"Generado: {generated_at:%d/%m/%Y %H:%M:%S}"
    ws["A2"].font = Font(italic=True, color="595959")
    if data.flt.corte_hasta:
        ws["A3"] = f"Fecha de corte: {data.flt.corte_hasta:%d/%m/%Y} (inclusive)"
        ws["A3"].font = Font(italic=True, color="595959")
    ws["A4"] = "RESUMEN DE PREMIOS GANADOS POR PAÍS"
    ws["A4"].font = _FONT_TITLE

    hr = 5
    for i, (h, w) in enumerate([("País", 24), ("Total de ganadores", 22)], start=1):
        ws.cell(row=hr, column=i, value=h)
        ws.column_dimensions[get_column_letter(i)].width = w
    _style_header(ws, hr, 2)

    ct = country_totals(data)
    r = hr
    for r, rec in enumerate(ct.itertuples(), start=hr + 1):
        _put(ws, r, 1, rec.pais)
        _put(ws, r, 2, int(rec.ganadores), "int", "#,##0")
    tr = r + 1
    ws.cell(row=tr, column=1, value="TOTAL GENERAL")
    ws.cell(row=tr, column=2, value=int(ct["ganadores"].sum()))
    for c in range(1, 3):
        cell = ws.cell(row=tr, column=c)
        cell.fill, cell.font, cell.border = _FILL_TOTAL, Font(bold=True), _BORDER
        if c > 1:
            cell.number_format = "#,##0"
    ws.freeze_panes = f"A{hr + 1}"


def _country_sheet(wb: Workbook, pais: str, g: pd.DataFrame, corte_hasta):
    ws = wb.create_sheet(pais)
    ncols = len(CLIENT_COLUMNS)
    ws["A1"] = f"Ganadores — {pais}"
    ws["A1"].font = _FONT_TITLE
    ws["A2"] = "Total de ganadores:"
    ws["C2"] = len(g)
    ws["A2"].font = Font(bold=True)
    ws["C2"].font = Font(bold=True)
    ws["C2"].number_format = "#,##0"
    ws["C2"].fill = _FILL_TOTAL
    if corte_hasta:
        ws["A3"] = f"Fecha de corte: {corte_hasta:%d/%m/%Y} (inclusive)"
        ws["A3"].font = Font(italic=True, color="595959")

    for i, (head, _, width, _) in enumerate(CLIENT_COLUMNS, start=1):
        ws.cell(row=HEADER_ROW, column=i, value=head)
        ws.column_dimensions[get_column_letter(i)].width = width
    _style_header(ws, HEADER_ROW, ncols)

    g = g.sort_values(["fecha_hora_ganador", "registro_ganador_id"], kind="mergesort")
    for r, rec in enumerate(g[CLIENT_KEYS].itertuples(index=False), start=HEADER_ROW + 1):
        d = rec._asdict()
        for i, (_, key, _, kind) in enumerate(CLIENT_COLUMNS, start=1):
            _put(ws, r, i, d[key], kind)

    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(ncols)}{max(HEADER_ROW + len(g), HEADER_ROW)}"
    ws.freeze_panes = f"A{HEADER_ROW + 1}"


def write_client_workbook(path: str, data: WinnersData, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    wb = Workbook()
    _summary_sheet(wb, data, generated_at)
    for pais in countries(data):
        _country_sheet(
            wb, pais, data.detail[data.detail["pais"] == pais], data.flt.corte_hasta)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    return path
