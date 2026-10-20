"""
main.py
Orquestador principal de Pulse Reports: ejecuta todas las queries, genera el PDF,
evalúa alertas y despacha notificaciones.

Uso:
    python main.py                                    # todas las secciones
    python main.py --sections premios_hoy             # solo premios entregados hoy
    python main.py --sections trivias premios_hoy     # varias secciones
    python main.py --sections historico config_gp reclamos

Secciones disponibles:
    trivias       Rendimiento y participación de trivias
    premios_hoy   Premios entregados en el día actual
    historico     Histórico y promedio de premios
    config_gp     Configuración GP (stock y premios)
    reclamos      Estado de premios reclamados
"""
import argparse
import logging
import os
from datetime import date, datetime

from config.settings import AppConfig, config
from notifications.senders import build_summary, dispatch_all
from queries.premios import (
    get_config_gp,
    get_cupones,
    get_historico,
    get_premios_hoy,
    get_reclamos,
)
from queries.trivias import get_rendimiento, get_resultado
from reports.alerts import (
    AlertReport,
    check_errores_entrega,
    check_sin_entregas,
    check_stock_gp,
)
from reports.charts import (
    chart_estado_reclamos,
    chart_historico_premios,
    chart_participacion_trivias,
    chart_premios_hoy,
    chart_rendimiento_trivias,
    chart_stock_gp,
)
from reports.pdf_generator import GPReportPDF
from utils.logger import setup_logging

logger = logging.getLogger(__name__)

ALL_SECTIONS = {"trivias", "premios_hoy", "historico", "config_gp", "reclamos"}

EMPTY_DF_FACTORY = __import__("pandas").DataFrame


def _empty():
    return EMPTY_DF_FACTORY()


def _combinar_premios_hoy(df_hn, df_sv):
    """Combina los premios entregados hoy de ambos países en un solo
    DataFrame con columna 'pais', para tabla detallada y notificaciones."""
    import pandas as pd
    frames = []
    if df_hn is not None and not df_hn.empty:
        t = df_hn.copy()
        t["pais"] = "Honduras"
        frames.append(t)
    if df_sv is not None and not df_sv.empty:
        t = df_sv.copy()
        t["pais"] = "El Salvador"
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    combinado = pd.concat(frames, ignore_index=True)
    combinado = combinado.sort_values(
        ["pais", "entregados_hoy"], ascending=[True, False]
    ).reset_index(drop=True)
    return combinado


# ─────────────────────────────────────────────────────────────────────────────
# Lógica principal
# ─────────────────────────────────────────────────────────────────────────────

def run_report(cfg: AppConfig | None = None,
               sections: set[str] | None = None) -> str | None:
    """
    Genera el reporte completo y retorna la ruta del PDF generado.

    Args:
        cfg:      Configuración de la app. Por defecto usa la instancia global.
        sections: Conjunto de secciones a incluir. None = todas.

    Retorna None si hubo un error fatal.
    """
    cfg = cfg or config
    sections = sections if sections is not None else ALL_SECTIONS
    now = datetime.now()

    logger.info("=" * 60)
    logger.info("Iniciando Pulse Reports — %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Secciones activas: %s", sorted(sections))
    logger.info("=" * 60)

    # ── 1. Recolección de datos (solo lo que se necesita) ────────────────────
    logger.info("Ejecutando queries Honduras…")
    df_triv_hn  = get_rendimiento(cfg.db_hn, cfg.trivia_ids_hn)  if "trivias"     in sections else _empty()
    df_resul_hn = get_resultado(cfg.db_hn, cfg.trivia_ids_hn)    if "trivias"     in sections else _empty()
    df_prem_hn  = get_premios_hoy(cfg.db_hn, cfg.all_reward_ids) if "premios_hoy" in sections else _empty()
    df_hist_hn  = get_historico(cfg.db_hn, cfg.all_reward_ids)   if "historico"   in sections else _empty()
    df_gp_hn    = get_config_gp(cfg.db_hn)                       if "config_gp"   in sections else _empty()
    df_cup_hn   = get_cupones(cfg.db_hn, cfg.reward_ids_hn,
                              "2026-05-21 00:00:00", "2026-07-17 23:59:59") if "config_gp" in sections else _empty()
    df_recl_hn  = get_reclamos(cfg.db_hn)                        if "reclamos"    in sections else _empty()

    logger.info("Ejecutando queries El Salvador…")
    df_triv_sv  = get_rendimiento(cfg.db_sv, cfg.trivia_ids_sv)  if "trivias"     in sections else _empty()
    df_resul_sv = get_resultado(cfg.db_sv, cfg.trivia_ids_sv)    if "trivias"     in sections else _empty()
    df_prem_sv  = get_premios_hoy(cfg.db_sv, cfg.all_reward_ids) if "premios_hoy" in sections else _empty()
    df_hist_sv  = get_historico(cfg.db_sv, cfg.all_reward_ids)   if "historico"   in sections else _empty()
    df_gp_sv    = get_config_gp(cfg.db_sv)                       if "config_gp"   in sections else _empty()
    df_cup_sv   = get_cupones(cfg.db_sv, cfg.reward_ids_sv,
                              "2026-05-21 00:00:00", "2026-07-17 23:59:59") if "config_gp" in sections else _empty()
    df_recl_sv  = get_reclamos(cfg.db_sv)                        if "reclamos"    in sections else _empty()

    df_premios_hoy_combinado = (
        _combinar_premios_hoy(df_prem_hn, df_prem_sv)
        if "premios_hoy" in sections else _empty()
    )

    # ── 2. Alertas ───────────────────────────────────────────────────────────
    logger.info("Evaluando alertas…")
    alert_report = AlertReport()

    if "config_gp" in sections:
        check_stock_gp(df_gp_hn, cfg.alerts, "Honduras", alert_report)
        check_stock_gp(df_gp_sv, cfg.alerts, "El Salvador", alert_report)
    if "reclamos" in sections:
        check_errores_entrega(df_recl_hn, "Honduras", alert_report)
        check_errores_entrega(df_recl_sv, "El Salvador", alert_report)
    if "premios_hoy" in sections:
        check_sin_entregas(df_prem_hn, cfg.reward_ids_hn, "Honduras", alert_report)
        check_sin_entregas(df_prem_sv, cfg.reward_ids_sv, "El Salvador", alert_report)

    logger.info("Alertas detectadas: %d", len(alert_report.alerts))

    # ── 3. Gráficos (solo secciones activas) ─────────────────────────────────
    logger.info("Generando gráficos…")

    c_rend_hn = chart_rendimiento_trivias(df_triv_hn, "Honduras")    if "trivias"     in sections else None
    c_rend_sv = chart_rendimiento_trivias(df_triv_sv, "El Salvador") if "trivias"     in sections else None
    c_part_hn = chart_participacion_trivias(df_triv_hn, "Honduras")  if "trivias"     in sections else None
    c_part_sv = chart_participacion_trivias(df_triv_sv, "El Salvador") if "trivias"   in sections else None
    c_prem_hn = chart_premios_hoy(df_prem_hn, "Honduras")            if "premios_hoy" in sections else None
    c_prem_sv = chart_premios_hoy(df_prem_sv, "El Salvador")         if "premios_hoy" in sections else None
    c_hist_hn = chart_historico_premios(df_hist_hn, "Honduras")      if "historico"   in sections else None
    c_hist_sv = chart_historico_premios(df_hist_sv, "El Salvador")   if "historico"   in sections else None
    c_gp_hn   = chart_stock_gp(df_gp_hn, "Honduras")                if "config_gp"   in sections else None
    c_gp_sv   = chart_stock_gp(df_gp_sv, "El Salvador")             if "config_gp"   in sections else None
    c_recl    = chart_estado_reclamos(df_recl_hn, "Honduras")        if "reclamos"    in sections else None

    # ── 4. PDF ───────────────────────────────────────────────────────────────
    os.makedirs(cfg.output_dir, exist_ok=True)
    suffix = "_".join(sorted(sections)) if sections != ALL_SECTIONS else "full"
    pdf_name = f"reporte_gp_{now.strftime('%Y%m%d_%H%M')}_{suffix}.pdf"
    pdf_path = os.path.join(cfg.output_dir, pdf_name)

    logger.info("Construyendo PDF → %s", pdf_path)
    try:
        report = GPReportPDF(pdf_path, generated_at=now)

        report.add_kpi_cards(df_prem_hn, df_prem_sv, df_triv_hn, df_triv_sv)
        report.add_alerts_section(alert_report)

        if "trivias" in sections:
            report.add_trivias_section(
                df_triv_hn, df_triv_sv,
                c_rend_hn, c_rend_sv,
                c_part_hn, c_part_sv,
            )
        if "premios_hoy" in sections:
            report.add_premios_hoy_section(
                df_prem_hn, df_prem_sv, c_prem_hn, c_prem_sv,
                df_combinado=df_premios_hoy_combinado,
            )
        if "historico" in sections:
            report.add_historico_section(df_hist_hn, df_hist_sv, c_hist_hn, c_hist_sv)
        if "config_gp" in sections:
            report.add_config_gp_section(df_gp_hn, df_gp_sv, c_gp_hn, c_gp_sv)
        if "reclamos" in sections:
            report.add_reclamos_section(df_recl_hn, c_recl)

        report.build(alert_report)
    except Exception as exc:
        logger.error("Error al generar el PDF: %s", exc, exc_info=True)
        pdf_path = None

    # ── 5. Notificaciones ────────────────────────────────────────────────────
    summary = build_summary(
        alert_report,
        df_prem_hn, df_prem_sv,
        df_triv_hn, df_triv_sv,
        generated_at=now,
        df_premios_hoy_detalle=df_premios_hoy_combinado,
    )
    dispatch_all(cfg.notifications, summary, pdf_path, generated_at=now)

    logger.info("Reporte completado. PDF: %s", pdf_path)
    return pdf_path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> set[str]:
    parser = argparse.ArgumentParser(
        description="Pulse Reports — generador de reportes GP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Secciones disponibles:
  trivias       Rendimiento y participación de trivias
  premios_hoy   Premios entregados en el día actual
  historico     Histórico y promedio de premios
  config_gp     Configuración GP (stock y premios)
  reclamos      Estado de premios reclamados

Ejemplos:
  python main.py
  python main.py --sections premios_hoy
  python main.py --sections trivias premios_hoy
  python main.py --sections historico config_gp reclamos
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
    setup_logging()
    sections = _parse_args()
    run_report(sections=sections)