"""
export_ganadores_cliente.py
Reporte Excel de ganadores para el cliente Grupo UNO: Resumen General (consolidado
por país) + una hoja por país. Independiente del reporte interno
(export_ganadores.py); comparte con él consultas, conexión y validaciones.

Uso:
    python export_ganadores_cliente.py                 # genera output/cliente/*.xlsx
    python export_ganadores_cliente.py --telegram      # genera y envía SOLO este archivo
    python export_ganadores_cliente.py --pais GT --desde 2026-10-01

Solo ejecuta SELECT. El Excel contiene datos personales: no lo subas al repositorio.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import config
from export_ganadores import (
    _FLAGS, _connection_hint, _date, _print_checks, _send_to_telegram,
)
from queries.ganadores import (
    CAMPAIGN_CATALOG, DEFAULT_TZ, WinnersFilter, fetch_winners, normalize_detail,
)
from reports.ganadores_cliente_excel import write_client_workbook
from reports.ganadores_cliente_validation import validate_client_workbook
from reports.ganadores_validation import build_summary, country_totals, run_checks
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Exporta a Excel los ganadores de Gane Premios para el cliente Grupo UNO.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pais", nargs="+", metavar="PAIS", help="Guatemala/GT, El Salvador/SV, Honduras/HN.")
    p.add_argument("--campania", nargs="+", type=int, metavar="ID",
                   choices=[c["campaign_id"] for c in CAMPAIGN_CATALOG])
    p.add_argument("--desde", type=_date, metavar="YYYY-MM-DD")
    p.add_argument("--hasta", type=_date, metavar="YYYY-MM-DD")
    p.add_argument("--estado", nargs="+", metavar="CODIGO")
    p.add_argument("--tz", default=DEFAULT_TZ)
    p.add_argument("--output", metavar="RUTA",
                   help="Ruta del .xlsx (default output/cliente/ganadores_cliente_grupouno_<fecha>.xlsx).")
    p.add_argument("--telegram", action="store_true",
                   help="Tras generar y validar, envía este Excel al grupo de Telegram configurado.")
    p.add_argument("--yes", action="store_true",
                   help="Con --telegram: omite la pregunta de confirmación (el grupo se verifica igualmente).")
    return p.parse_args(argv)


def build_client_caption(data, generated_at: datetime) -> str:
    """Solo conteos agregados, sin datos personales."""
    ct = country_totals(data)
    lines = ["Reporte de Ganadores para Cliente – Gane Premios Grupo UNO", "",
             f"Fecha y hora de generación: {generated_at:%d/%m/%Y %H:%M}", ""]
    if data.flt.corte_hasta:
        lines.append(f"Fecha de corte: {data.flt.corte_hasta:%d/%m/%Y} (inclusive)")
        lines.append("")
    for r in ct.itertuples():
        lines.append(f"{_FLAGS.get(r.pais, '')} {r.pais}: {r.ganadores} ganadores".strip())
    lines += ["", f"Total general: {int(ct['ganadores'].sum())} ganadores", "",
              "Se adjunta el reporte Excel con el resumen por país y el detalle de ganadores."]
    return "\n".join(lines)


def _apply_client_period(flt: WinnersFilter, now: datetime) -> datetime:
    zone = ZoneInfo(flt.tz)
    local_now = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    automatic_cutoff = today_start.date() - timedelta(days=1)

    flt.usar_inicio_campania = True
    flt.hasta_exclusivo = today_start
    flt.corte_hasta = min(automatic_cutoff, flt.hasta) if flt.hasta else automatic_cutoff
    return local_now


def _format_campaign_starts(starts: dict[int, datetime], tz: str) -> str:
    zone = ZoneInfo(tz)
    values = []
    for campaign_id, start in sorted(starts.items()):
        local_start = start.astimezone(zone) if start.tzinfo else start.replace(tzinfo=zone)
        values.append(f"{campaign_id}={local_start:%d/%m/%Y %H:%M:%S}")
    return ", ".join(values)


def _print_client_checks(checks) -> None:
    print("============================================")
    print("VALIDACIÓN DEL REPORTE DE GANADORES")
    print("============================================")
    for check in checks:
        name = "Cantidad de premios" if not check.blocking else check.name
        if check.ok:
            print(f"[OK] {name}")
        elif check.blocking:
            print(f"[REVISAR] {name}: {check.detail}")
        else:
            print(f"[AVISO] {name}")
            print(check.detail)
    print("============================================")


def _confirm_prize_observations() -> bool:
    print("\nExisten observaciones en la cantidad de premios.")
    if not sys.stdin.isatty():
        print("Telegram: no se envió porque se requiere confirmación interactiva. "
              "El Excel se conserva.", file=sys.stderr)
        return False
    try:
        answer = input("¿Desea enviar el reporte a Telegram de todas formas? (s/n): ")
    except EOFError:
        print("Telegram: no se recibió confirmación. El Excel se conserva.", file=sys.stderr)
        return False
    return answer.strip().lower() in ("s", "si", "sí")


def run(args: argparse.Namespace) -> int:
    flt = WinnersFilter(paises=args.pais or [], campanias=args.campania or [],
                        desde=args.desde, hasta=args.hasta, estados=args.estado or [], tz=args.tz)
    try:
        execution_at = _apply_client_period(flt, datetime.now(ZoneInfo(args.tz)))
    except ZoneInfoNotFoundError:
        print(f"Error: zona horaria desconocida '{args.tz}'.", file=sys.stderr)
        return 2
    if flt.desde and flt.hasta and flt.desde > flt.hasta:
        print("Error: --desde es posterior a --hasta.", file=sys.stderr)
        return 2
    if flt.desde and flt.desde > flt.corte_hasta:
        print("Error: --desde es posterior a la fecha de corte automática.", file=sys.stderr)
        return 2
    logger.info(
        "Reporte de cliente: ejecución=%s; corte=%s (inclusive); zona horaria=%s.",
        execution_at.isoformat(), flt.corte_hasta.isoformat(), flt.tz)
    try:
        data = fetch_winners(config.db_hn, flt)
    except Exception as exc:
        logger.error("No se pudo obtener los ganadores: %s", exc)
        print(f"Error: {exc}{_connection_hint(config.db_hn)}", file=sys.stderr)
        return 1

    configured_starts = _format_campaign_starts(flt.inicios_campania, flt.tz)
    logger.info("Inicio configurado por campaña (%s): %s.", flt.tz, configured_starts)
    data.detail = normalize_detail(data.detail, flt.tz)
    checks = run_checks(data, build_summary(data))

    now = execution_at
    path = args.output or os.path.join(
        config.output_dir, "cliente", f"ganadores_cliente_grupouno_{now:%Y%m%d_%H%M}.xlsx")
    write_client_workbook(path, data, generated_at=now)
    checks += validate_client_workbook(path, data)

    print(f"Filtros: {flt.describe()}\n")
    ct = country_totals(data)
    print(ct.to_string(index=False))
    total_winners = int(ct["ganadores"].sum())
    total_prizes = int(ct["premios"].sum())
    print(f"TOTAL ganadores={total_winners}  premios={total_prizes}\n")
    logger.info(
        "Reporte hasta el corte: ganadores=%d; premios=%d.",
        total_winners, total_prizes)
    _print_client_checks(checks)
    for c in checks:
        if not c.ok:
            if c.blocking:
                logger.warning("Validación fallida — %s: %s", c.name, c.detail)
            else:
                logger.warning("Validación con observaciones — %s.", c.name)
    critical_ok = all(c.ok for c in checks if c.blocking)
    warnings = [c for c in checks if not c.ok and not c.blocking]
    print(f"\nArchivo: {os.path.abspath(path)}")

    if args.telegram:
        if not critical_ok:
            print("Telegram: NO se envió porque hay validaciones en REVISAR. "
                  "El Excel se conserva.", file=sys.stderr)
            return 1
        if warnings and not _confirm_prize_observations():
            print("Telegram: envío cancelado. El Excel se conserva.", file=sys.stderr)
            return 3
        return _send_to_telegram(path, data, now, args.yes,
                                 caption=build_client_caption(data, now))
    return 0 if critical_ok else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    sys.exit(run(_parse_args()))
