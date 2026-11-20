"""Generacion del libro ejecutivo de dinamicas con openpyxl."""
import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from zoneinfo import ZoneInfo

from queries.dinamicas import DynamicsData
from reports.dinamicas_validation import Check, build_metrics

_NAVY = "1F3864"
_PALE_BLUE = "D9EAF7"
_GREEN = "E2F0D9"
_RED = "E7E6E6"
_GRAY = "F2F2F2"
_WHITE = "FFFFFF"
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill("solid", fgColor=_NAVY)
_TOTAL_FILL = PatternFill("solid", fgColor=_PALE_BLUE)
_DATE_FORMAT = "dd/mm/yyyy hh:mm:ss"


def _native(value):
    if value is None or value is pd.NA:
        return None
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if hasattr(value, "item"):
        value = value.item()
    return value


def _write(cell, value, kind="text", nd_on_missing=False):
    value = _native(value)
    if value is None and nd_on_missing:
        value = "N/D"
    if isinstance(value, str) and value.startswith("="):
        cell.value = value
        cell.data_type = "s"
    else:
        cell.value = value
    if kind == "number" and value is not None:
        cell.number_format = "#,##0"
    elif kind == "percent" and value is not None:
        cell.number_format = "0.0%"
    elif kind == "datetime" and value is not None:
        cell.number_format = _DATE_FORMAT
    elif kind == "text":
        cell.number_format = "@"
    cell.border = _BORDER
    cell.alignment = Alignment(
        horizontal="right" if kind in {"number", "percent"} else "left",
        vertical="center", wrap_text=kind == "text")


def _header(ws, row: int, columns: int):
    for column in range(1, columns + 1):
        cell = ws.cell(row=row, column=column)
        cell.fill = _HEADER_FILL
        cell.font = Font(bold=True, color=_WHITE)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
    ws.row_dimensions[row].height = 30


def _title(ws, text: str, subtitle: str | None = None):
    _write(ws["A1"], text)
    ws["A1"].font = Font(bold=True, size=16, color=_NAVY)
    if subtitle:
        _write(ws["A2"], subtitle)
        ws["A2"].font = Font(italic=True, color="595959")


def _local_datetime(value, tz: str):
    value = _native(value)
    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    return value


def _dashboard(wb: Workbook, data: DynamicsData):
    ws = wb.active
    ws.title = "DASHBOARD"
    dynamics = data.dinamica
    variants, total = build_metrics(data)
    _title(ws, dynamics.nombre, dynamics.descripcion)

    period = "Sin filtro de fechas"
    if data.periodo_desde or data.periodo_hasta:
        period = (
            f"{data.periodo_desde.isoformat() if data.periodo_desde else 'inicio'} "
            f"a {data.periodo_hasta.isoformat() if data.periodo_hasta else 'corte'}")
    meta = [
        ("País", dynamics.pais),
        ("Generado", data.generated_at.strftime("%d/%m/%Y %H:%M:%S %Z")),
        ("Período analizado", period),
    ]
    for row, (label, value) in enumerate(meta, start=3):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, color=_NAVY)
        _write(ws.cell(row=row, column=2), value)

    headers = ["Indicador"] + [v["nombre"] for v in dynamics.variantes] + ["TOTAL GENERAL"]
    hr = 7
    for column, heading in enumerate(headers, start=1):
        _write(ws.cell(row=hr, column=column), heading)
    _header(ws, hr, len(headers))
    sections = [
        ("HISTORIAS", [
            ("historias_vistas", "Total de vistas", "number"),
            ("historias_usuarios_vistas", "Usuarios únicos que visualizaron", "number"),
        ]),
        ("RASPÁDITAS", [
            ("raspadita_total", "Total de registros", "number"),
            ("raspadita_usuarios", "Participantes únicos", "number"),
            ("raspadita_ganadores", "Resultados ganadores", "number"),
            ("raspadita_perdedores", "Resultados perdedores", "number"),
            ("raspadita_sin_resultado", "Registros sin resultado", "number"),
            ("raspadita_pct_ganadores", "% ganadores sobre clasificados", "percent"),
        ]),
        ("RETOS", [
            ("reto_usuarios_aceptaron", "Usuarios que aceptaron", "number"),
            ("reto_usuarios_completaron", "Usuarios que completaron", "number"),
            ("reto_usuarios_pendientes", "Usuarios aceptados sin cumplimiento", "number"),
            ("reto_pct_cumplimiento", "% cumplimiento sobre aceptados", "percent"),
            ("reto_registros_cumplimiento", "Registros de cumplimiento", "number"),
        ]),
        ("CUPONES", [
            ("cupones_asignados", "Cupones asignados", "number"),
            ("cupones_clientes", "Clientes únicos con cupón", "number"),
            ("cupones_canjeados", "Canjeados", "number"),
            ("cupones_sin_canjear", "Sin canjear", "number"),
            ("cupones_vencidos", "Vencidos", "number"),
            ("cupones_cancelados", "Cancelados", "number"),
            ("cupones_pct_canje", "% canje sobre asignados", "percent"),
        ]),
    ]
    metric_rows, chart_rows = {}, []
    row_index = hr + 1
    for section, metrics in sections:
        ws.cell(row=row_index, column=1, value=section)
        ws.cell(row=row_index, column=1).font = Font(bold=True, color=_NAVY)
        ws.cell(row=row_index, column=1).fill = PatternFill("solid", fgColor=_GRAY)
        row_index += 1
        for key, label, kind in metrics:
            ws.cell(row=row_index, column=1, value=label)
            for col, variant_metrics in enumerate(variants, start=2):
                _write(ws.cell(row=row_index, column=col), variant_metrics.get(key), kind,
                       nd_on_missing=True)
            _write(ws.cell(row=row_index, column=len(headers)), total.get(key), kind,
                   nd_on_missing=True)
            for col in range(1, len(headers) + 1):
                ws.cell(row=row_index, column=col).border = _BORDER
                if col == len(headers):
                    ws.cell(row=row_index, column=col).fill = _TOTAL_FILL
                    ws.cell(row=row_index, column=col).font = Font(bold=True)
            metric_rows[key] = row_index
            row_index += 1
        if section == "RASPÁDITAS":
            chart_rows = [
                metric_rows["raspadita_total"],
                metric_rows["raspadita_ganadores"],
                metric_rows["raspadita_perdedores"],
            ]

    ws.column_dimensions["A"].width = 45
    for col in range(2, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 22
    ws.freeze_panes = f"B{hr + 1}"

    if len(dynamics.variantes) and chart_rows:
        chart = BarChart()
        chart.type = "col"
        chart.style = 10
        chart.title = "Registros de raspadita por variante"
        chart.y_axis.title = "Registros"
        chart.x_axis.title = "Indicador"
        for row in chart_rows:
            chart.add_data(
                Reference(ws, min_col=1, max_col=1 + len(variants),
                          min_row=row, max_row=row),
                titles_from_data=True, from_rows=True)
        chart.set_categories(Reference(ws, min_col=2, max_col=1 + len(variants),
                                       min_row=hr, max_row=hr))
        chart.height = 6
        chart.width = 12
        chart.title = "Raspaditas: registros, ganadores y perdedores"
        ws.add_chart(chart, f"{get_column_letter(len(headers) + 1)}{hr}")
    return ws


def _detail_sheet(wb: Workbook, name: str, title: str, data: DynamicsData,
                  frame: pd.DataFrame, columns: list[tuple[str, str, str]],
                  highlights: dict[str, str] | None = None):
    ws = wb.create_sheet(name)
    _title(ws, title, f"{data.dinamica.nombre} · {data.dinamica.pais}")
    hr = 4
    if columns:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 20
    for index, (heading, _, _) in enumerate(columns, start=1):
        ws.cell(row=hr, column=index, value=heading)
    _header(ws, hr, len(columns))
    for row_index, record in enumerate(frame.to_dict("records"), start=hr + 1):
        for column_index, (_, key, kind) in enumerate(columns, start=1):
            value = record.get(key)
            if kind == "datetime":
                value = _local_datetime(value, data.dinamica.zona_horaria)
            _write(ws.cell(row=row_index, column=column_index), value, kind)
            if highlights and key in highlights:
                status = record.get(key)
                color = highlights[key].get(status)
                if color:
                    ws.cell(row=row_index, column=column_index).fill = PatternFill(
                        "solid", fgColor=color)
    for index, (heading, _, _) in enumerate(columns, start=1):
        kind = columns[index - 1][2]
        width = 22 if kind == "datetime" else max(14, min(34, len(heading) + 4))
        ws.column_dimensions[get_column_letter(index)].width = width
    last_col = get_column_letter(len(columns))
    ws.auto_filter.ref = f"A{hr}:{last_col}{max(hr, hr + len(frame))}"
    ws.freeze_panes = f"A{hr + 1}"
    return ws


def _details(wb: Workbook, data: DynamicsData):
    variant_by_campaign = {
        v["campania_raspadita_id"]: v["nombre"] for v in data.dinamica.variantes}
    scratch = data.scratch.copy()
    if not scratch.empty:
        scratch["variante"] = scratch["campania_id"].map(variant_by_campaign)
        scratch["resultado_texto"] = scratch["resultado"].map(
            lambda value: "Ganador" if value == 1 else (
                "Perdedor" if value == 0 else (
                    "Sin resultado" if pd.isna(value) else f"No esperado: {value}")))
    else:
        for key in ("variante", "resultado_texto"):
            scratch[key] = pd.Series(dtype=object)
    _detail_sheet(wb, "PARTICIPANTES RASPADITA", "Participantes de raspadita", data,
                  scratch, [
                      ("Fecha de registro", "fecha_registro", "datetime"),
                      ("ID del registro", "registro_id", "number"),
                      ("ID del cliente", "cliente_id", "number"),
                      ("Nombre", "nombre", "text"),
                      ("Apellido", "apellido", "text"),
                      ("Correo", "correo", "text"),
                      ("Teléfono", "telefono", "text"),
                      ("Variante", "variante", "text"),
                      ("ID de campaña", "campania_id", "number"),
                      ("Resultado", "resultado_texto", "text"),
                  ], {"resultado_texto": {"Ganador": _GREEN, "Perdedor": _RED}})

    winners = scratch[scratch["resultado"] == 1].copy() if "resultado" in scratch else scratch.iloc[0:0]
    _detail_sheet(wb, "GANADORES", "Ganadores clasificados por raspadita", data,
                  winners, [
                      ("Fecha", "fecha_registro", "datetime"),
                      ("ID del cliente", "cliente_id", "number"),
                      ("Nombre", "nombre", "text"),
                      ("Apellido", "apellido", "text"),
                      ("Correo", "correo", "text"),
                      ("Teléfono", "telefono", "text"),
                      ("Variante", "variante", "text"),
                      ("Campaña", "campania_id", "number"),
                  ])

    losers = scratch[scratch["resultado"] == 0].copy() if "resultado" in scratch else scratch.iloc[0:0]
    _detail_sheet(wb, "PERDEDORES", "Resultados perdedores de raspadita", data,
                  losers, [
                      ("Fecha", "fecha_registro", "datetime"),
                      ("ID del cliente", "cliente_id", "number"),
                      ("Nombre", "nombre", "text"),
                      ("Apellido", "apellido", "text"),
                      ("Correo", "correo", "text"),
                      ("Teléfono", "telefono", "text"),
                      ("Variante", "variante", "text"),
                      ("Campaña", "campania_id", "number"),
                      ("Resultado", "resultado_texto", "text"),
                  ])

    challenges = data.challenges.copy()
    if not challenges.empty:
        challenges["variante"] = challenges["reto_id"].map(
            {v["reto_id"]: v["nombre"] for v in data.dinamica.variantes})
        challenges["estado_cumplimiento"] = challenges["completado"].map(
            {True: "Completado", False: "Pendiente"})
    else:
        challenges["variante"] = pd.Series(dtype=object)
        challenges["estado_cumplimiento"] = pd.Series(dtype=object)
    _detail_sheet(wb, "RETOS", "Aceptaciones y cumplimiento de retos", data,
                  challenges, [
                      ("Variante", "variante", "text"),
                      ("ID del reto", "reto_id", "number"),
                      ("Nombre del reto", "nombre_reto", "text"),
                      ("ID del cliente", "cliente_id", "number"),
                      ("Nombre", "nombre", "text"),
                      ("Correo", "correo", "text"),
                      ("Fecha de aceptación", "fecha_aceptacion", "datetime"),
                      ("Estado de cumplimiento", "estado_cumplimiento", "text"),
                      ("Fecha de cumplimiento", "fecha_cumplimiento", "datetime"),
                      ("Registros de cumplimiento", "cantidad_registros_cumplimiento", "number"),
                  ], {"estado_cumplimiento": {"Completado": _GREEN}})

    coupons = data.coupons.copy()
    if not coupons.empty:
        coupons["variante"] = coupons["cupon_id"].map(
            {v["cupon_id"]: v["nombre"] for v in data.dinamica.variantes})
        status_name = {0: "Canjeado", 1: "Sin canjear", 2: "Vencido", -1: "Cancelado"}
        coupons["estado_texto"] = coupons["estado"].map(
            lambda value: status_name.get(value, f"No esperado: {value}"))
    else:
        coupons["variante"] = pd.Series(dtype=object)
        coupons["estado_texto"] = pd.Series(dtype=object)
    _detail_sheet(wb, "CUPONES", "Cupones asignados", data, coupons, [
        ("ID de asignación", "asignacion_id", "number"),
        ("ID del cliente", "cliente_id", "number"),
        ("Nombre", "nombre", "text"),
        ("Apellido", "apellido", "text"),
        ("Correo", "correo", "text"),
        ("Teléfono", "telefono", "text"),
        ("Variante", "variante", "text"),
        ("ID de cupón", "cupon_id", "number"),
        ("Cupón", "nombre_cupon", "text"),
        ("Fecha de asignación", "fecha_asignacion", "datetime"),
        ("Estado de canje", "estado_texto", "text"),
    ], {"estado_texto": {"Canjeado": _GREEN, "Cancelado": _RED}})


def write_workbook(path: str, data: DynamicsData, checks: list[Check]) -> str:
    wb = Workbook()
    _dashboard(wb, data)
    _details(wb, data)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    wb.close()
    return path
