"""
reports/ganadores_validation.py
Resumen por campaña y conciliación del detalle de ganadores contra consultas
de control (sin JOINs) ejecutadas directamente sobre "CustomerPrizeWins".
"""
from dataclasses import dataclass

import pandas as pd

from queries.ganadores import WinnersData, sheet_name

DT_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def _fmt_status(s: pd.Series) -> str:
    if s.empty:
        return ""
    counts = s.astype(str).where(s.notna(), "(nulo)").value_counts().sort_index()
    return " | ".join(f"{k}: {v}" for k, v in counts.items())


def build_summary(data: WinnersData) -> pd.DataFrame:
    rows = []
    for c in data.catalog:
        g = data.detail[data.detail["campania_id"] == c["campaign_id"]]
        rows.append({
            "pais": c["pais"],
            "campania_id": c["campaign_id"],
            "nombre_campania": data.campaign_names.get(c["campaign_id"]) or "(sin nombre)",
            "tipo_premio": c["premio"],
            "registros": len(g),
            "clientes_unicos": int(g["cliente_id"].nunique()),
            "total_premios": int(pd.to_numeric(g["cantidad_premios"], errors="coerce").fillna(0).sum()),
            "primer_ganador": g["fecha_hora_ganador"].min() if len(g) else pd.NaT,
            "ultimo_ganador": g["fecha_hora_ganador"].max() if len(g) else pd.NaT,
            "estados": _fmt_status(g["estado"]),
            "hoja": sheet_name(c),
        })
    return pd.DataFrame(rows)


def country_totals(data: WinnersData) -> pd.DataFrame:
    """Ganadores y premios por país calculados directo del detalle (sin pasar por
    el resumen por campaña). Orden del catálogo."""
    det = data.detail.assign(
        cantidad_premios=pd.to_numeric(data.detail["cantidad_premios"], errors="coerce").fillna(0))
    paises = list(dict.fromkeys(c["pais"] for c in data.catalog))
    rows = []
    for p in paises:
        g = det[det["pais"] == p]
        rows.append({"pais": p, "ganadores": len(g), "premios": int(g["cantidad_premios"].sum())})
    return pd.DataFrame(rows, columns=["pais", "ganadores", "premios"])


def run_checks(data: WinnersData, summary: pd.DataFrame) -> list[Check]:
    det, ctl = data.detail, data.control.set_index("campania_id")
    ids = [c["campaign_id"] for c in data.catalog]
    checks: list[Check] = []

    # 1. Todas las campañas del catálogo seleccionado aparecen en el resumen
    missing = sorted(set(ids) - set(summary["campania_id"]))
    checks.append(Check("Campañas en el resumen", not missing,
                        f"{len(summary)}/{len(ids)} campañas" +
                        (f"; faltan {missing}" if missing else "")))

    # 2. Cada ganador pertenece a una campaña del catálogo con su país/premio
    cat = {c["campaign_id"]: (c["pais"], c["premio"]) for c in data.catalog}
    bad = det[[(cat.get(r.campania_id) != (r.pais, r.tipo_premio))
               for r in det.itertuples()]]
    checks.append(Check("Ganador ↔ campaña/país/premio", bad.empty,
                        "Todos coinciden con el catálogo" if bad.empty
                        else f"{len(bad)} registros inconsistentes"))

    # 3. Sin duplicados por JOINs
    dups = int(det["registro_ganador_id"].duplicated().sum())
    checks.append(Check("Sin duplicados por JOIN", dups == 0,
                        f"{dups} IDs de registro repetidos"))

    # Compra asociada: nunca posterior al premio; sin compra no elimina al ganador
    if "fecha_venta" in det.columns:
        posterior = int((det["fecha_venta"] > det["fecha_hora_ganador"]).sum())
        sin_compra = int(det["transaccion_venta_id"].isna().sum())
        checks.append(Check("Compra asociada ≤ fecha del premio", posterior == 0,
                            f"{posterior} compras posteriores; {sin_compra} ganadores sin "
                            f"compra candidata (conservados). Asociación temporal: no confirmada"))

    # 4, 5, 6, 8. Conciliación por campaña contra el control sin JOINs
    def _ctl(cid, col, default=0):
        return ctl.loc[cid, col] if cid in ctl.index else default

    diffs_rows, diffs_cust, diffs_sum, diffs_dt = [], [], [], []
    for r in summary.itertuples():
        cid = r.campania_id
        if r.registros != int(_ctl(cid, "registros")):
            diffs_rows.append(f"{cid}: detalle={r.registros} vs BD={int(_ctl(cid, 'registros'))}")
        if r.clientes_unicos != int(_ctl(cid, "clientes_unicos")):
            diffs_cust.append(f"{cid}: detalle={r.clientes_unicos} vs BD={int(_ctl(cid, 'clientes_unicos'))}")
        if r.total_premios != int(_ctl(cid, "total_premios")):
            diffs_sum.append(f"{cid}: detalle={r.total_premios} vs BD={int(_ctl(cid, 'total_premios'))}")
        exp_first, exp_last = _ctl(cid, "primer_ganador", None), _ctl(cid, "ultimo_ganador", None)
        got_first = None if pd.isna(r.primer_ganador) else r.primer_ganador.strftime(DT_FMT)
        got_last = None if pd.isna(r.ultimo_ganador) else r.ultimo_ganador.strftime(DT_FMT)
        if (got_first, got_last) != (exp_first, exp_last):
            diffs_dt.append(f"{cid}: detalle=({got_first},{got_last}) vs BD=({exp_first},{exp_last})")

    total_ctl = int(ctl["registros"].sum()) if len(ctl) else 0
    checks.append(Check("Registros por hoja = COUNT(*) BD", not diffs_rows,
                        f"Total hojas={len(det)} / BD={total_ctl}" +
                        ("; " + "; ".join(diffs_rows) if diffs_rows else "")))
    checks.append(Check("Clientes únicos por campaña = BD", not diffs_cust,
                        "Coinciden en todas las campañas" if not diffs_cust else "; ".join(diffs_cust)))
    checks.append(Check("Suma totalPrizes = BD", not diffs_sum,
                        f"Suma detalle={int(summary['total_premios'].sum())}" +
                        ("; " + "; ".join(diffs_sum) if diffs_sum else "")))
    checks.append(Check("Fechas/horas sin desplazamiento (primer/último)", not diffs_dt,
                        f"Zona horaria de salida: {data.flt.tz}" +
                        ("; " + "; ".join(diffs_dt) if diffs_dt else "")))

    # A zero-prize record is an observation, not necessarily a data-integrity failure.
    sin_premios = int(ctl["sin_premios"].sum()) if len(ctl) else 0
    total_prem, total_reg = int(summary["total_premios"].sum()), len(det)
    cantidad = pd.to_numeric(det["cantidad_premios"], errors="coerce")
    anomalos = det[cantidad.isna() | (cantidad <= 0)]
    diferencia = total_reg - total_prem
    detalle_anomalias = [
        f"ID: {r.registro_ganador_id}; Cliente: {r.cliente_id}; "
        f"Campaña: {r.campania_id}; País: {r.pais}; "
        f"totalPrizes: {r.cantidad_premios if pd.notna(r.cantidad_premios) else 'NULL'}"
        for r in anomalos.itertuples()
    ]
    detalle_premios = (
        f"Registros de ganadores: {total_reg}\n"
        f"Premios contabilizados: {total_prem}\n"
        f"Diferencia (registros - premios): {diferencia}\n"
        f"Registros con observaciones: {sin_premios}"
    )
    if detalle_anomalias:
        detalle_premios += "\n\n" + "\n".join(detalle_anomalias)
    checks.append(Check("totalPrizes vs registros (informativo)",
                        sin_premios == 0, detalle_premios,
                        blocking=False))

    # 7. Registros con contacto incompleto se conservan
    inc = {
        "sin nombre": int(det["nombre_completo"].isna().sum()),
        "sin correo": int(det["correo_electronico"].isna().sum()),
        "sin teléfono": int(det["telefono"].isna().sum()),
        "sin identificación": int(det["identificacion"].isna().sum()),
    }
    checks.append(Check("Contacto incompleto conservado", not diffs_rows,
                        "Incluidos en las hojas: " + ", ".join(f"{k}={v}" for k, v in inc.items())))
    # 9. Resumen por país: cada país = suma de sus campañas y total = tabla principal
    ct = country_totals(data)
    por_campania = summary.groupby("pais", sort=False)[["registros", "total_premios"]].sum()
    pais_diffs = [
        f"{r.pais}: país={r.ganadores}/{r.premios} vs campañas="
        f"{int(por_campania.loc[r.pais, 'registros'])}/{int(por_campania.loc[r.pais, 'total_premios'])}"
        for r in ct.itertuples()
        if (r.ganadores, r.premios) != (int(por_campania.loc[r.pais, "registros"]),
                                        int(por_campania.loc[r.pais, "total_premios"]))
    ]
    tot_pais = (int(ct["ganadores"].sum()), int(ct["premios"].sum()))
    tot_main = (int(summary["registros"].sum()), int(summary["total_premios"].sum()))
    checks.append(Check("Resumen por país = suma de campañas", not pais_diffs,
                        "; ".join(pais_diffs) if pais_diffs else
                        ", ".join(f"{r.pais}={r.ganadores}" for r in ct.itertuples())))
    checks.append(Check("Total general por país = tabla principal", tot_pais == tot_main,
                        f"por país={tot_pais[0]}/{tot_pais[1]} vs principal={tot_main[0]}/{tot_main[1]}"))
    return checks


def validate_workbook(path: str, data: WinnersData, summary: pd.DataFrame) -> list[Check]:
    """Reabre el .xlsx generado y verifica hojas, conteos y pertenencia."""
    from openpyxl import load_workbook

    HEADER_ROW = 4
    wb = load_workbook(path)
    expected = ["Resumen General"] + [sheet_name(c) for c in data.catalog]
    checks = [Check("Excel abre y contiene las hojas esperadas", wb.sheetnames == expected,
                    f"{len(wb.sheetnames)} hojas: {wb.sheetnames}")]

    texts = [str(c.value) for row in wb["Resumen General"].iter_rows() for c in row
             if isinstance(c.value, str)]
    checks.append(Check("Resumen: incluye tabla por país", 
                        "RESUMEN DE PREMIOS GANADOS POR PAÍS" in texts,
                        "Sección presente" if "RESUMEN DE PREMIOS GANADOS POR PAÍS" in texts else "Falta la sección"))
    checks.append(Check("Resumen: sin sección de conciliación",
                        not any("onciliaci" in t for t in texts),
                        "No aparece en el Excel"))

    ws0 = wb["Resumen General"]
    ct = country_totals(data)
    rows = {r[0]: r for r in ws0.iter_rows(values_only=True) if isinstance(r[0], str)}
    bad = []
    for p in ct.itertuples():
        row = rows.get(p.pais)
        got = (row[4], row[5]) if row and p.pais in rows and isinstance(row[4], int) else None
        if got != (p.ganadores, p.premios):
            bad.append(f"{p.pais}: hoja={got} esperado={(p.ganadores, p.premios)}")
    totals = [r for r in ws0.iter_rows(values_only=True) if r[0] == "TOTAL GENERAL"]
    exp = (int(ct["ganadores"].sum()), int(ct["premios"].sum()))
    if len(totals) != 2:
        bad.append(f"se esperaban 2 filas TOTAL GENERAL, hay {len(totals)}")
    else:
        if (totals[0][4], totals[0][6]) != exp:
            bad.append(f"TOTAL GENERAL principal={(totals[0][4], totals[0][6])}")
        if (totals[1][4], totals[1][5]) != exp:
            bad.append(f"TOTAL GENERAL por país={(totals[1][4], totals[1][5])}")
    formulas = [c.coordinate for row in ws0.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=")]
    if formulas:
        bad.append(f"fórmulas en Resumen: {formulas}")
    checks.append(Check("Resumen por país: valores numéricos = datos", not bad,
                        "OK, sin fórmulas" if not bad else "; ".join(bad)))

    problems = []
    for r in summary.itertuples():
        ws = wb[r.hoja]
        n = ws.max_row - HEADER_ROW
        camp_col = [c.value for c in ws[HEADER_ROW]].index("ID campaña") + 1
        foreign = [row for row in ws.iter_rows(min_row=HEADER_ROW + 1, min_col=camp_col,
                                               max_col=camp_col, values_only=True)
                   if int(row[0]) != r.campania_id]
        if n != r.registros or foreign:
            problems.append(f"{r.hoja}: filas={n} esperado={r.registros}, ajenas={len(foreign)}")
        if ws.auto_filter.ref is None or ws.freeze_panes is None:
            problems.append(f"{r.hoja}: sin autofiltro o panel inmovilizado")
    checks.append(Check("Hojas: filas y campaña correctas", not problems,
                        "OK" if not problems else "; ".join(problems)))
    wb.close()
    return checks
