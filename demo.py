"""
demo.py
──────────────────────────────────────────────────────────────────────────────
Genera un reporte Pulse Reports completo usando datos de DEMO (sin PostgreSQL).

Uso:
    python demo.py                                    # todas las secciones
    python demo.py --sections premios_hoy             # solo premios entregados hoy
    python demo.py --sections trivias premios_hoy     # varias secciones
    python demo.py --sections historico config_gp reclamos

Secciones disponibles:
    trivias       Rendimiento y participación de trivias
    premios_hoy   Premios entregados en el día actual
    historico     Histórico y promedio de premios
    config_gp     Configuración GP (stock y premios)
    reclamos      Estado de premios reclamados

Salida:
    output/reporte_gp_YYYYMMDD_HHMM_<secciones>.pdf
    (consola) resumen de notificaciones
──────────────────────────────────────────────────────────────────────────────
"""

import argparse
import os
import logging
from datetime import datetime

import pandas as pd

from reports.alerts import AlertReport, check_stock_gp, check_errores_entrega, check_sin_entregas
from reports.charts import (
    chart_rendimiento_trivias,
    chart_participacion_trivias,
    chart_premios_hoy,
    chart_historico_premios,
    chart_stock_gp,
    chart_estado_reclamos,
)
from reports.pdf_generator import GPReportPDF
from notifications.senders import build_summary
from config.settings import AlertConfig
from utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

ALL_SECTIONS = {"trivias", "premios_hoy", "historico", "config_gp", "reclamos"}


# ══════════════════════════════════════════════════════════════════════════════
#  DATOS DE DEMO
# ══════════════════════════════════════════════════════════════════════════════

def _demo_trivias(pais: str) -> pd.DataFrame:
    base = {
        "Honduras": [
            (93, "Trivia Semana 1 — GP Honduras", 1200, 870, 540, 310, 230),
            (94, "Trivia Semana 2 — GP Honduras", 1100, 790, 430, 280, 150),
            (95, "Trivia Semana 3 — GP Honduras", 1350, 1020, 610, 395, 215),
            (96, "Trivia Semana 4 — GP Honduras", 980,  710,  390, 240, 150),
            (97, "Trivia Semana 5 — GP Honduras", 1050, 800,  470, 310, 160),
            (98, "Trivia Semana 6 — GP Honduras", 1180, 850,  500, 320, 180),
            (99, "Trivia Semana 7 — GP Honduras", 900,  650,  350, 200, 150),
            (100,"Trivia Semana 8 — GP Honduras", 1300, 950,  580, 370, 210),
        ],
        "El Salvador": [
            (113,"Trivia Semana 1 — GP El Salvador", 1400, 1020, 620, 380, 240),
            (114,"Trivia Semana 2 — GP El Salvador", 1250, 890,  480, 310, 170),
            (115,"Trivia Semana 3 — GP El Salvador", 1500, 1100, 690, 440, 250),
            (116,"Trivia Semana 4 — GP El Salvador", 1100, 780,  420, 260, 160),
            (117,"Trivia Semana 5 — GP El Salvador", 1320, 940,  530, 340, 190),
            (118,"Trivia Semana 6 — GP El Salvador", 980,  700,  380, 230, 150),
            (119,"Trivia Semana 7 — GP El Salvador", 1150, 830,  460, 290, 170),
            (120,"Trivia Semana 8 — GP El Salvador", 1400, 1000, 600, 380, 220),
        ],
    }
    rows = base[pais]
    return pd.DataFrame(rows, columns=[
        "id", "trivia", "enviados", "abiertos", "veces_jugado", "correctas", "incorrectas"
    ])


def _demo_premios_hoy(pais: str) -> pd.DataFrame:
    data = {
        "Honduras": [
            (280, "Gift Card Q200",    45),
            (282, "Bono Celular 50",   32),
            (283, "Voucher Tienda",    18),
            (284, "Premio Especial",    7),
        ],
        "El Salvador": [
            (298, "Gift Card $10",     38),
            (300, "Recarga Celular",   27),
            (301, "Cupón Supermercado",21),
            (302, "Premio Digital",    12),
            (303, "Bono Combustible",   9),
        ],
    }
    return pd.DataFrame(data[pais], columns=["reward_id", "premio", "entregados_hoy"])


def _demo_historico(pais: str) -> pd.DataFrame:
    data = {
        "Honduras": [
            ("Gift Card Q200",    41, 1250, 18.4),
            ("Bono Celular 50",   30,  980, 14.4),
            ("Voucher Tienda",    20,  540,  7.9),
            ("Premio Especial",    5,  210,  3.1),
        ],
        "El Salvador": [
            ("Gift Card $10",     35, 1100, 16.2),
            ("Recarga Celular",   25,  870, 12.8),
            ("Cupón Supermercado",18,  620,  9.1),
            ("Premio Digital",    10,  340,  5.0),
            ("Bono Combustible",   7,  190,  2.8),
        ],
    }
    return pd.DataFrame(data[pais],
                        columns=["premio","entregados_ayer","entregados_total","promedio_x_dia"])


def _demo_config_gp(pais: str) -> pd.DataFrame:
    cid = 376 if pais == "Honduras" else 406
    data = {
        "Honduras": [
            (cid, "GP Honduras Q2", "Supermercados XYZ", "Gift Card Q200",    "ganador",  0.15, "global",    2000, 1250,  750, 0),
            (cid, "GP Honduras Q2", "Supermercados XYZ", "Bono Celular 50",   "ganador",  0.10, "global",    1500,  980,  520, 0),
            (cid, "GP Honduras Q2", "Supermercados XYZ", "Voucher Tienda",    "ganador",  0.08, "por tienda",  800,  540,  260, 35),
            (cid, "GP Honduras Q2", "Supermercados XYZ", "Premio Especial",   "ganador",  0.05, "global",     300,  210,   90, 0),
            (cid, "GP Honduras Q2", "Supermercados XYZ", "Sin Premio",        "perdedor", 0.62, "global",       0,    0,    0, 0),
        ],
        "El Salvador": [
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Gift Card $10",     "ganador",  0.15, "global",    2500, 1100, 1400, 0),
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Recarga Celular",   "ganador",  0.12, "global",    2000,  870, 1130, 0),
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Cupón Supermercado","ganador",  0.08, "por tienda",1000,  620,  380, 42),
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Premio Digital",    "ganador",  0.05, "global",     500,  340,  160, 0),
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Bono Combustible",  "ganador",  0.03, "global",     200,  190,   10, 0),
            (cid, "GP El Salvador Q2","Cadena Retail SV", "Sin Premio",        "perdedor", 0.57, "global",       0,    0,    0, 0),
        ],
    }
    cols = ["campaign_id","campana","comercio","premio","tipo","prob",
            "alcance","stock_total","ganados","faltan_por_entregar","tiendas"]
    return pd.DataFrame(data[pais], columns=cols)


def _demo_reclamos() -> pd.DataFrame:
    estados = [
        "Redimido", "Reclamado", "Pendiente", "Error en entrega",
        "Redimido", "Reclamado", "Disponible para retiro", "En traslado",
        "Expirado", "Redimido", "Reclamado", "Redimido", "Pendiente",
        "Error en entrega", "Redimido",
    ]
    return pd.DataFrame({
        "estado_premio": estados,
        "premio": ["Gift Card Q200"] * len(estados),
        "nombre_ganador": [f"Cliente Demo {i+1}" for i in range(len(estados))],
        "cedula": [f"0801-{1990+i}-{10000+i}" for i in range(len(estados))],
        "email": [f"cliente{i+1}@demo.com" for i in range(len(estados))],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_demo(sections: set[str] | None = None) -> str:
    sections = sections if sections is not None else ALL_SECTIONS
    now = datetime.now()

    logger.info("=" * 60)
    logger.info("Pulse Reports — MODO DEMO — %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Secciones activas: %s", sorted(sections))
    logger.info("=" * 60)

    _e = pd.DataFrame

    # ── Datos (solo lo necesario) ─────────────────────────────────────────────
    df_triv_hn = _demo_trivias("Honduras")    if "trivias"     in sections else _e()
    df_triv_sv = _demo_trivias("El Salvador") if "trivias"     in sections else _e()
    df_prem_hn = _demo_premios_hoy("Honduras")    if "premios_hoy" in sections else _e()
    df_prem_sv = _demo_premios_hoy("El Salvador") if "premios_hoy" in sections else _e()
    df_hist_hn = _demo_historico("Honduras")    if "historico"   in sections else _e()
    df_hist_sv = _demo_historico("El Salvador") if "historico"   in sections else _e()
    df_gp_hn   = _demo_config_gp("Honduras")    if "config_gp"   in sections else _e()
    df_gp_sv   = _demo_config_gp("El Salvador") if "config_gp"   in sections else _e()
    df_recl    = _demo_reclamos()               if "reclamos"    in sections else _e()

    # ── Alertas ───────────────────────────────────────────────────────────────
    acfg = AlertConfig(stock_threshold=0.10, trivia_drop_threshold=0.25)
    alert = AlertReport()
    if "config_gp" in sections:
        check_stock_gp(df_gp_hn, acfg, "Honduras", alert)
        check_stock_gp(df_gp_sv, acfg, "El Salvador", alert)
    if "reclamos" in sections:
        check_errores_entrega(df_recl, "Honduras/SV", alert)
    if "premios_hoy" in sections:
        check_sin_entregas(df_prem_hn, [280,282,283,284], "Honduras", alert)
        check_sin_entregas(df_prem_sv, [298,300,301,302,303], "El Salvador", alert)
    logger.info("Alertas detectadas: %d", len(alert.alerts))

    # ── Gráficos ──────────────────────────────────────────────────────────────
    logger.info("Generando gráficos…")
    c_rend_hn = chart_rendimiento_trivias(df_triv_hn, "Honduras")      if "trivias"     in sections else None
    c_rend_sv = chart_rendimiento_trivias(df_triv_sv, "El Salvador")   if "trivias"     in sections else None
    c_part_hn = chart_participacion_trivias(df_triv_hn, "Honduras")    if "trivias"     in sections else None
    c_part_sv = chart_participacion_trivias(df_triv_sv, "El Salvador") if "trivias"     in sections else None
    c_prem_hn = chart_premios_hoy(df_prem_hn, "Honduras")              if "premios_hoy" in sections else None
    c_prem_sv = chart_premios_hoy(df_prem_sv, "El Salvador")           if "premios_hoy" in sections else None
    c_hist_hn = chart_historico_premios(df_hist_hn, "Honduras")        if "historico"   in sections else None
    c_hist_sv = chart_historico_premios(df_hist_sv, "El Salvador")     if "historico"   in sections else None
    c_gp_hn   = chart_stock_gp(df_gp_hn, "Honduras")                  if "config_gp"   in sections else None
    c_gp_sv   = chart_stock_gp(df_gp_sv, "El Salvador")               if "config_gp"   in sections else None
    c_recl    = chart_estado_reclamos(df_recl, "Honduras + El Salvador") if "reclamos"  in sections else None

    # ── PDF ───────────────────────────────────────────────────────────────────
    os.makedirs("output", exist_ok=True)
    suffix = "_".join(sorted(sections)) if sections != ALL_SECTIONS else "full"
    pdf_name = f"reporte_gp_{now.strftime('%Y%m%d_%H%M')}_{suffix}.pdf"
    pdf_path = os.path.join("output", pdf_name)

    logger.info("Construyendo PDF → %s", pdf_path)
    report = GPReportPDF(pdf_path, generated_at=now)
    report.add_kpi_cards(df_prem_hn, df_prem_sv, df_triv_hn, df_triv_sv)
    report.add_alerts_section(alert)

    if "trivias" in sections:
        report.add_trivias_section(df_triv_hn, df_triv_sv,
                                    c_rend_hn, c_rend_sv,
                                    c_part_hn, c_part_sv)
    if "premios_hoy" in sections:
        report.add_premios_hoy_section(df_prem_hn, df_prem_sv, c_prem_hn, c_prem_sv)
    if "historico" in sections:
        report.add_historico_section(df_hist_hn, df_hist_sv, c_hist_hn, c_hist_sv)
    if "config_gp" in sections:
        report.add_config_gp_section(df_gp_hn, df_gp_sv, c_gp_hn, c_gp_sv)
    if "reclamos" in sections:
        report.add_reclamos_section(df_recl, c_recl)

    report.build(alert)

    size_kb = os.path.getsize(pdf_path) // 1024
    logger.info("PDF listo: %s (%d KB)", pdf_path, size_kb)

    # ── Resumen en consola ────────────────────────────────────────────────────
    summary = build_summary(alert, df_prem_hn, df_prem_sv, df_triv_hn, df_triv_sv,
                             generated_at=now)
    print("\n" + "═" * 60)
    print(summary)
    print("═" * 60)
    print(f"\n✅  PDF generado: {pdf_path}  ({size_kb} KB)\n")

    return pdf_path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> set[str]:
    parser = argparse.ArgumentParser(
        description="Pulse Reports DEMO — genera reporte con datos ficticios.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Secciones disponibles:
  trivias       Rendimiento y participación de trivias
  premios_hoy   Premios entregados en el día actual
  historico     Histórico y promedio de premios
  config_gp     Configuración GP (stock y premios)
  reclamos      Estado de premios reclamados

Ejemplos:
  python demo.py
  python demo.py --sections premios_hoy
  python demo.py --sections trivias premios_hoy
  python demo.py --sections historico config_gp reclamos
        """,
    )
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=sorted(ALL_SECTIONS),
        metavar="SECCION",
        default=None,
        help="Secciones a incluir en el PDF (por defecto: todas).",
    )
    args = parser.parse_args()
    return set(args.sections) if args.sections else ALL_SECTIONS


if __name__ == "__main__":
    run_demo(sections=_parse_args())