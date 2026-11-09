"""
reports/ganadores_cliente_validation.py
Validaciones internas del Excel del cliente (no se escriben en el archivo).
Las comprobaciones de datos (duplicados, totales, pertenencia, conciliación con BD)
son las mismas del reporte interno: ganadores_validation.run_checks.
"""
from openpyxl import load_workbook

from queries.ganadores import WinnersData
from reports.ganadores_cliente_excel import (
    CLIENT_COLUMNS, HEADER_ROW, SUMMARY_SHEET, countries,
)
from reports.ganadores_validation import Check, country_totals


def validate_client_workbook(path: str, data: WinnersData) -> list[Check]:
    wb = load_workbook(path)
    paises = countries(data)
    checks = [Check("Cliente: exactamente las hojas esperadas",
                    wb.sheetnames == [SUMMARY_SHEET] + paises,
                    f"{len(wb.sheetnames)} hojas: {wb.sheetnames}")]
    if data.flt.corte_hasta:
        expected_cutoff = (
            f"Fecha de corte: {data.flt.corte_hasta:%d/%m/%Y} (inclusive)")
        checks.append(Check(
            "Cliente: fecha de corte visible",
            wb[SUMMARY_SHEET]["A3"].value == expected_cutoff,
            f"Esperada: {expected_cutoff}; encontrada: {wb[SUMMARY_SHEET]['A3'].value}"))
    ct = country_totals(data)
    rows = list(wb[SUMMARY_SHEET].iter_rows(values_only=True))
    bad = []
    for p in ct.itertuples():
        row = next((r for r in rows if r[0] == p.pais), None)
        if not row or row[1] != p.ganadores:
            bad.append(f"{p.pais}: hoja={row[1] if row else None} esperado={p.ganadores}")
    tot = next((r for r in rows if r[0] == "TOTAL GENERAL"), None)
    exp = int(ct["ganadores"].sum())
    if not tot or tot[1] != exp:
        bad.append(f"TOTAL GENERAL={tot[1] if tot else None} esperado={exp}")
    if any(len(r) > 2 and r[2] is not None for r in rows):
        bad.append("el resumen tiene más de dos columnas")
    if int(ct["ganadores"].sum()) != len(data.detail):
        bad.append("la suma por país no coincide con el detalle")
    checks.append(Check("Cliente: resumen por país y total general", not bad,
                        "OK" if not bad else "; ".join(bad)))

    formulas = [f"{ws.title}!{c.coordinate}" for ws in wb for row in ws.iter_rows()
                for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    checks.append(Check("Cliente: sin fórmulas (solo valores)", not formulas,
                        "OK" if not formulas else f"fórmulas: {formulas[:5]}"))

    extra = [t for r in rows for t in r if isinstance(t, str)
             and any(w in t.lower() for w in ("onciliaci", "campaña 8"))]
    checks.append(Check("Cliente: resumen sin información técnica ni de campañas", not extra,
                        "OK" if not extra else f"{extra[:3]}"))

    problems = []
    for pais in paises:
        ws = wb[pais]
        g = data.detail[data.detail["pais"] == pais]
        n = ws.max_row - HEADER_ROW
        heads = [c.value for c in ws[HEADER_ROW]]
        camp_col = heads.index("ID campaña") + 1
        ids = [r[0] for r in ws.iter_rows(min_row=HEADER_ROW + 1, min_col=camp_col,
                                          max_col=camp_col, values_only=True)]
        allowed = {c["campaign_id"] for c in data.catalog if c["pais"] == pais}
        time_col = heads.index("Fecha y hora ganó") + 1
        times = [r[0] for r in ws.iter_rows(
            min_row=HEADER_ROW + 1, min_col=time_col, max_col=time_col,
            values_only=True) if r[0] is not None]
        if n != len(g):
            problems.append(f"{pais}: filas={n} esperado={len(g)}")
        if any(int(i) not in allowed for i in ids):
            problems.append(f"{pais}: campañas ajenas")
        if heads != [c[0] for c in CLIENT_COLUMNS]:
            problems.append(f"{pais}: columnas distintas")
        if times != sorted(times):
            problems.append(f"{pais}: no está ordenada por fecha")
        if ws.auto_filter.ref is None or ws.freeze_panes is None:
            problems.append(f"{pais}: sin filtro o panel inmovilizado")
    checks.append(Check("Cliente: hojas por país (filas, campañas, orden)", not problems,
                        "OK" if not problems else "; ".join(problems)))
    wb.close()
    return checks
