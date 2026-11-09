"""
reporte_diario_premios.py
Reporte diario Gane Premios (GT, SV, HN) enviado solo como texto por Telegram.

Uso:
    python reporte_diario_premios.py --preview    # muestra el mensaje; no envía
    python reporte_diario_premios.py              # muestra y pregunta si enviar
    python reporte_diario_premios.py --telegram   # muestra y envía sin preguntar

Solo ejecuta SELECT. No genera archivos.
"""
import argparse
import logging
import sys

import pandas as pd

from config.settings import config
from notifications.senders import TelegramError, send_telegram_text
from queries.premios_quemados import load_premios_quemados
from queries.reporte_diario_premios import fetch_reporte_diario
from reports.reporte_diario_mensaje import build_mensaje
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reporte diario Gane Premios por Telegram.",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Muestra el mensaje sin enviar.")
    g.add_argument("--telegram", action="store_true", help="Envía directamente sin confirmación.")
    return p.parse_args(argv)


def _enviar(texto: str) -> int:
    try:
        send_telegram_text(config.notifications, texto)
    except TelegramError as exc:
        logger.error("Telegram: %s", exc)
        print(f"Error al enviar a Telegram: {exc}", file=sys.stderr)
        return 1
    print("Mensaje enviado a Telegram.")
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        df = fetch_reporte_diario(config.db_hn)
        fecha = pd.to_datetime(df.iloc[0]["fecha"]).date()
        quemados, advertencias = load_premios_quemados(fecha)
        texto = build_mensaje(df, quemados, advertencias)
    except Exception as exc:
        logger.error("No se pudo generar el reporte: %s", exc)
        print(f"Error: no se pudo generar el reporte; no se envió nada. ({exc})", file=sys.stderr)
        return 1

    print(texto)
    if args.preview:
        return 0
    if args.telegram:
        return _enviar(texto)

    try:
        resp = input("\n¿Desea enviar este reporte a Telegram? (s/n): ").strip().lower()
    except EOFError:
        resp = ""
    if resp != "s":
        print("Envío cancelado.")
        return 0
    return _enviar(texto)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    sys.exit(main())
