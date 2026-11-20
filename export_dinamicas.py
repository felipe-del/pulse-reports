"""Exporta el reporte Excel de una dinamica promocional TAPP.

Uso:
    python export_dinamicas.py
    python export_dinamicas.py --dinamica motagua_olimpia_2026
    python export_dinamicas.py --preview
    python export_dinamicas.py --check
"""
import argparse
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.dinamicas import DynamicsConfigError, load_dynamics
from config.settings import config
from queries.dinamicas import check_connection, fetch_dynamics
from reports.dinamicas_excel import write_workbook
from reports.dinamicas_validation import build_metrics, information_state, run_checks
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Fecha invalida '{value}', use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("Use el formato YYYY-MM-DD.")
    return parsed


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Genera reportes Excel de dinamicas promocionales TAPP.")
    parser.add_argument("--dinamica", help="Clave de una dinamica en config/dinamicas.json.")
    parser.add_argument("--preview", action="store_true",
                        help="Muestra indicadores sin generar un archivo.")
    parser.add_argument("--check", action="store_true",
                        help="Verifica SELECT, esquema, IDs configurados y eventos disponibles.")
    parser.add_argument("--output", help="Ruta del archivo .xlsx.")
    parser.add_argument("--desde", type=_date, help="Inicio inclusivo (YYYY-MM-DD).")
    parser.add_argument("--hasta", type=_date, help="Fin inclusivo (YYYY-MM-DD).")
    return parser.parse_args(argv)


def _print_check(dynamics, schema_status, id_status):
    failed = False
    print(f"Dinámica: {dynamics.nombre} ({dynamics.key})")
    print("SELECT 1: OK")
    print("\nCompatibilidad del esquema:")
    for source, status in schema_status.items():
        print(f"  {source}: {status}")
        if status.startswith("NO"):
            failed = True
    print("\nIDs configurados:")
    for status in id_status:
        # Descriptions are diagnostic labels, not report data; bound output size.
        safe = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[correo]", status)
        safe = re.sub(r"\b(?:\+?\d[\d ()-]{7,}\d)\b", "[telefono]", safe)
        print(f"  {safe[:300]}")
        if "; faltan " in status or "no verificable" in status:
            failed = True
    print("\nCTA se mantiene N/D hasta que eventos_cta incluya una descripcion "
          "confirmada de story_actions.description.")
    return 1 if failed else 0


def _print_preview(data):
    variants, total = build_metrics(data)
    columns = [
        ("Vistas", "historias_vistas"),
        ("Usuarios unicos que visualizaron", "historias_usuarios_vistas"),
        ("CTA usuarios", "historias_usuarios_cta"),
        ("Raspaditas", "raspadita_total"),
        ("Raspadita participantes unicos", "raspadita_usuarios"),
        ("Ganadores", "raspadita_ganadores"),
        ("Perdedores", "raspadita_perdedores"),
        ("Sin resultado", "raspadita_sin_resultado"),
        ("Retos aceptaciones", "reto_aceptaciones"),
        ("Retos completados", "reto_usuarios_completaron"),
        ("Retos pendientes", "reto_usuarios_pendientes"),
        ("Cupones asignados", "cupones_asignados"),
        ("Cupones canjeados", "cupones_canjeados"),
        ("Cupones sin canjear", "cupones_sin_canjear"),
    ]
    headers = ["Indicador"] + [row["nombre"] for row in variants] + ["TOTAL GENERAL"]
    print(f"{data.dinamica.nombre} · {data.dinamica.pais}")
    print(f"Zona horaria: {data.dinamica.zona_horaria}")
    print(f"Estado: {information_state(data)}")
    print(" | ".join(headers))
    for label, key in columns:
        values = [row.get(key) if row.get(key) is not None else "N/D" for row in variants]
        aggregate = total.get(key) if total.get(key) is not None else "N/D"
        print(" | ".join(map(str, [label, *values, aggregate])))
    print("\nFuentes:")
    for source, status in data.source_status.items():
        print(f"  {source}: {status}")


def _report_failed_checks(checks):
    failed = [check for check in checks if not check.ok]
    for check in failed:
        message = f"Validación no aprobada: {check.name} — {check.detail}"
        logger.warning("%s", message)
        print(message, file=sys.stderr)


def run(args) -> int:
    try:
        dynamics = load_dynamics(selected=args.dinamica)
    except DynamicsConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2

    desde = args.desde if args.desde is not None else dynamics.fecha_inicio
    hasta = args.hasta if args.hasta is not None else dynamics.fecha_fin
    if desde and hasta and desde > hasta:
        print("Error: --desde no puede ser posterior a --hasta.", file=sys.stderr)
        return 2

    if args.check and args.preview:
        print("Error: --check y --preview son opciones excluyentes.", file=sys.stderr)
        return 2
    if args.output and Path(args.output).suffix.lower() != ".xlsx":
        print("Error: --output debe terminar en .xlsx.", file=sys.stderr)
        return 2

    if args.check:
        try:
            _, schema_status, id_status = check_connection(config.db_hn, dynamics)
        except Exception as exc:
            logger.error("Verificacion de BD fallida (%s).", type(exc).__name__)
            print(f"Error de conexión/verificación ({type(exc).__name__}).", file=sys.stderr)
            return 1
        return _print_check(dynamics, schema_status, id_status)

    generated_at = datetime.now(ZoneInfo(dynamics.zona_horaria))
    try:
        data = fetch_dynamics(config.db_hn, dynamics, generated_at, desde, hasta)
    except Exception as exc:
        # Avoid logging exception text from database drivers, which can include row data.
        logger.error("Consulta de dinamicas fallida (%s).", type(exc).__name__)
        print(f"Error al consultar las fuentes ({type(exc).__name__}); no se generó archivo.",
              file=sys.stderr)
        return 1

    metrics = build_metrics(data)
    checks = run_checks(data, metrics)
    _report_failed_checks(checks)
    if args.preview:
        _print_preview(data)
        return 0 if all(check.ok for check in checks if check.blocking) else 1

    path = args.output or os.path.join(
        config.output_dir, f"dinamica_{dynamics.key}_{generated_at:%Y%m%d_%H%M}.xlsx")
    try:
        write_workbook(path, data, checks)
    except Exception as exc:
        logger.error("No se pudo generar el Excel (%s).", type(exc).__name__)
        print(f"Error al generar el Excel ({type(exc).__name__}).", file=sys.stderr)
        return 1

    state = information_state(data, checks)
    print(f"Archivo: {os.path.abspath(path)}")
    print(f"Estado: {state}")
    return 0 if state == "COMPLETA" and all(
        check.ok for check in checks if check.blocking) else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    sys.exit(run(_parse_args()))
