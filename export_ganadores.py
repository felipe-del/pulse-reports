"""
export_ganadores.py
Exporta a Excel el detalle de ganadores de las campañas Gane Premios
(Guatemala, El Salvador, Honduras) de Grupo UNO.

Uso:
    python export_ganadores.py                              # todo, sin filtros
    python export_ganadores.py --preview                    # solo vista previa, sin archivo
    python export_ganadores.py --pais GT SV
    python export_ganadores.py --campania 818 825
    python export_ganadores.py --desde 2026-06-01 --hasta 2026-06-30
    python export_ganadores.py --estado 1
    python export_ganadores.py --output output\\ganadores.xlsx
    python export_ganadores.py --telegram                   # genera, valida y envía al grupo de Telegram

Solo ejecuta SELECT sobre la BD configurada (usa la conexión de solo lectura
existente). El Excel contiene datos personales: no lo subas al repositorio.
"""
import argparse
import logging
import os
import sys
from datetime import date, datetime

import pandas as pd

from config.settings import config
from notifications.senders import TelegramError, get_telegram_chat, send_telegram_document
from queries.ganadores import (
    CAMPAIGN_CATALOG, DEFAULT_TZ, WinnersFilter, check_connection, fetch_winners,
    normalize_detail,
)
from reports.ganadores_excel import write_workbook
from reports.ganadores_validation import build_summary, country_totals, run_checks, validate_workbook
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _date(txt: str) -> date:
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Fecha inválida '{txt}', use YYYY-MM-DD")


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Exporta ganadores de campañas Gane Premios a Excel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--pais", nargs="+", metavar="PAIS",
                   help="Guatemala/GT, El Salvador/SV, Honduras/HN.")
    p.add_argument("--campania", nargs="+", type=int, metavar="ID",
                   choices=[c["campaign_id"] for c in CAMPAIGN_CATALOG],
                   help="IDs de campaña del catálogo.")
    p.add_argument("--desde", type=_date, metavar="YYYY-MM-DD", help="Fecha inicial (inclusive).")
    p.add_argument("--hasta", type=_date, metavar="YYYY-MM-DD", help="Fecha final (inclusive).")
    p.add_argument("--estado", nargs="+", metavar="CODIGO", help="Valores de status del registro.")
    p.add_argument("--tz", default=DEFAULT_TZ, help=f"Zona horaria de las fechas (default {DEFAULT_TZ}).")
    p.add_argument("--output", metavar="RUTA", help="Ruta del .xlsx (default output/ganadores_gp_<fecha>.xlsx).")
    p.add_argument("--preview", action="store_true",
                   help="Muestra resumen y primeros registros; no genera archivo.")
    p.add_argument("--check", action="store_true",
                   help="Solo prueba la conexión: SELECT 1 y conteo por campaña.")
    p.add_argument("--preview-rows", type=int, default=5, help="Filas por campaña en la vista previa.")
    p.add_argument("--telegram", action="store_true",
                   help="Tras generar y validar el Excel, lo envía al grupo de Telegram configurado.")
    p.add_argument("--yes", action="store_true",
                   help="Con --telegram: omite la pregunta de confirmación del grupo "
                        "(el grupo se verifica igualmente).")
    return p.parse_args(argv)


def _print_summary(summary: pd.DataFrame) -> None:
    view = summary[["pais", "campania_id", "tipo_premio", "registros",
                    "clientes_unicos", "total_premios", "primer_ganador",
                    "ultimo_ganador", "estados"]]
    print(view.to_string(index=False))
    print(f"\nTOTAL registros={int(summary['registros'].sum())}  "
          f"totalPrizes={int(summary['total_premios'].sum())}")


def _print_checks(checks) -> None:
    for c in checks:
        if c.ok:
            print(f"[OK] {c.name}: {c.detail}")
        elif c.blocking:
            print(f"[REVISAR] {c.name}: {c.detail}")
        else:
            print(f"[ADVERTENCIA] {c.name} (informativa, no bloquea el envío):\n{c.detail}")


def _connection_hint(db) -> str:
    if db.host in ("localhost", "127.0.0.1", "::1"):
        return (
            f"\nLa BD se configura en .env como {db.host}:{db.port} ({db.name}), igual que main.py.\n"
            "Ese puerto es un túnel SSH que debe estar abierto ANTES de ejecutar el reporte:\n"
            f"  ssh -i <llave.pem> -L {db.port}:<host-RDS>:5432 <usuario>@<bastión> -N\n"
            "Déjalo corriendo en otra terminal y verifica con:\n"
            f"  Test-NetConnection localhost -Port {db.port}\n"
            "Luego prueba: python export_ganadores.py --check"
        )
    return ""


_FLAGS = {"Guatemala": "🇬🇹", "El Salvador": "🇸🇻", "Honduras": "🇭🇳"}


def build_telegram_message(data, generated_at: datetime) -> str:
    """Texto para Telegram: solo conteos agregados, sin datos personales."""
    ct = country_totals(data)
    lines = [
        "Reporte de Ganadores – Gane Premios Grupo UNO",
        "",
        f"Fecha y hora de generación: {generated_at:%d/%m/%Y %H:%M}",
        "",
    ]
    for r in ct.itertuples():
        lines.append(f"{_FLAGS.get(r.pais, '')} {r.pais}: {r.ganadores} ganadores".strip())
    lines += ["", f"Total general: {int(ct['ganadores'].sum())} ganadores"]
    if data.flt.describe() != WinnersFilter().describe():
        lines += ["", f"Filtros aplicados: {data.flt.describe()}"]
    lines += ["", "Se adjunta el reporte Excel con el detalle de los ganadores por país y campaña."]
    return "\n".join(lines)


def _telegram_target_error(assume_yes: bool) -> str | None:
    """Verifica que el destino sea un grupo y pide confirmación explícita
    (el Excel contiene datos personales). No imprime token ni chat_id.
    Devuelve None si se puede enviar; si no, la razón."""
    try:
        chat = get_telegram_chat(config.notifications)
    except TelegramError as exc:
        return f"no se pudo verificar el destino de Telegram — {exc}"
    if chat.get("type") not in ("group", "supergroup"):
        return f"el destino configurado es de tipo '{chat.get('type')}', no un grupo"
    title, members = chat.get("title", "(sin título)"), chat.get("members")
    print(f"\nDestino Telegram: grupo «{title}»"
          + (f" ({members} miembros)" if members else ""))
    if assume_yes:
        return None
    if not sys.stdin.isatty():
        return ("se requiere confirmación interactiva; ejecuta de forma interactiva o usa --yes "
                "tras confirmar el grupo")
    ans = input("El Excel contiene datos personales. ¿Enviarlo a ese grupo? [s/N]: ")
    if ans.strip().lower() in ("s", "si", "sí", "y", "yes"):
        return None
    return "envío cancelado por el usuario"


def _deliver_telegram(path: str, data, generated_at: datetime, assume_yes: bool,
                      caption: str | None = None) -> str | None:
    """Envía el Excel. Devuelve None solo si la API de Telegram confirmó el envío;
    en otro caso, la razón del fallo. No reintenta (evita envíos duplicados)."""
    reason = _telegram_target_error(assume_yes)
    if reason:
        return reason
    print("[INFO] Enviando archivo a Telegram...")
    try:
        msg = send_telegram_document(config.notifications, path,
                                     caption or build_telegram_message(data, generated_at))
    except TelegramError as exc:
        logger.error("Telegram: no se pudo enviar el Excel — %s", exc)
        return f"Telegram respondió con error — {exc}"
    except Exception as exc:
        logger.error("Telegram: error inesperado al enviar el Excel — %s", exc)
        return f"error inesperado al enviar ({type(exc).__name__}) — {exc}"
    logger.info("Telegram: Excel enviado (message_id=%s).", (msg or {}).get("message_id"))
    return None


def _send_to_telegram(path: str, data, generated_at: datetime, assume_yes: bool,
                      caption: str | None = None) -> int:
    """Envía el Excel ya generado e imprime el resultado real. Operación
    independiente de la exportación: si falla, el archivo se conserva.
    Devuelve 0 si Telegram confirmó el envío, 3 en caso contrario."""
    reason = _deliver_telegram(path, data, generated_at, assume_yes, caption)
    abs_path = os.path.abspath(path)
    if reason:
        print(f"[NO ENVIADO] {reason}. El Excel se conserva.", file=sys.stderr)
        print(f"\nArchivo: {abs_path}\nTelegram: NO ENVIADO")
        return 3
    print("[ENVIADO] Archivo enviado correctamente.")
    print(f"\nArchivo: {abs_path}\nTelegram: ENVIADO")
    return 0


def run_export(args: argparse.Namespace) -> int:
    if args.telegram and (args.preview or args.check):
        print("Error: --telegram no se puede combinar con --preview ni --check.", file=sys.stderr)
        return 2
    if config.db_hn.dsn() != config.db_sv.dsn():
        logger.warning("DB_HN y DB_SV difieren; el reporte usa la conexión DB_HN.")

    if args.check:
        try:
            counts = check_connection(config.db_hn)
        except Exception as exc:
            print(f"Error de conexión: {exc}{_connection_hint(config.db_hn)}", file=sys.stderr)
            return 1
        print("SELECT 1: OK")
        print(counts.to_string(index=False) if not counts.empty else "Sin ganadores en las 7 campañas.")
        return 0

    flt = WinnersFilter(
        paises=args.pais or [], campanias=args.campania or [],
        desde=args.desde, hasta=args.hasta, estados=args.estado or [], tz=args.tz,
    )
    if flt.desde and flt.hasta and flt.desde > flt.hasta:
        print("Error: --desde es posterior a --hasta.", file=sys.stderr)
        return 2

    try:
        data = fetch_winners(config.db_hn, flt)
    except Exception as exc:
        logger.error("No se pudo obtener los ganadores: %s", exc)
        print(f"Error: {exc}{_connection_hint(config.db_hn)}", file=sys.stderr)
        return 1

    data.detail = normalize_detail(data.detail, flt.tz)
    summary = build_summary(data)
    checks = run_checks(data, summary)

    print(f"Filtros: {flt.describe()}\n")
    _print_summary(summary)

    if args.preview:
        for c in data.catalog:
            g = data.detail[data.detail["campania_id"] == c["campaign_id"]]
            print(f"\n── {c['pais']} · {c['premio']} · {c['campaign_id']} ({len(g)} registros) ──")
            if len(g):
                print(g.head(args.preview_rows)[[
                    "registro_ganador_id", "cliente_id", "nombre_completo", "estado",
                    "fecha_hora_ganador"]].to_string(index=False))
        print()
        _print_checks(checks)
        return 0 if all(c.ok for c in checks if c.blocking) else 1

    now = datetime.now()
    path = args.output or os.path.join(
        config.output_dir, "interno", f"ganadores_gp_{now:%Y%m%d_%H%M}.xlsx")
    try:
        write_workbook(path, data, summary, generated_at=now)
        checks += validate_workbook(path, data, summary)
    except Exception as exc:
        logger.error("No se pudo generar/validar el Excel: %s", exc)
        print(f"[ERROR] No se pudo generar o validar el Excel: {exc}", file=sys.stderr)
        if args.telegram:
            print("[NO ENVIADO] El archivo no se generó o está corrupto.", file=sys.stderr)
            print("Telegram: NO ENVIADO")
        return 1

    print()
    _print_checks(checks)
    for c in checks:
        if not c.ok:
            logger.warning("Validación %s — %s: %s",
                           "fallida" if c.blocking else "informativa", c.name, c.detail)
    failed = [c for c in checks if not c.ok and c.blocking]
    informative = [c for c in checks if not c.ok and not c.blocking]
    if informative:
        print("\n[INFO] La diferencia no bloquea el envío.")
    abs_path = os.path.abspath(path)

    if args.telegram:
        if failed:
            names = "; ".join(c.name for c in failed)
            print(f"\n[NO ENVIADO] Validaciones críticas en REVISAR: {names}. "
                  "El Excel se conserva.", file=sys.stderr)
            print(f"\nArchivo: {abs_path}\nTelegram: NO ENVIADO")
            return 1
        return _send_to_telegram(path, data, now, assume_yes=args.yes)
    print(f"\nArchivo: {abs_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    sys.exit(run_export(_parse_args()))
