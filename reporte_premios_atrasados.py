"""Reporte de programación vigente Gane Premios por país, con envío opcional a Telegram.

Uso:
    python reporte_premios_atrasados.py
    python reporte_premios_atrasados.py --detalle

Solo ejecuta consultas SELECT. Nunca envía sin confirmación explícita.
"""
import argparse
import logging
import sys

from config.settings import config
from notifications.senders import TelegramError, send_telegram_text
from queries.reporte_premios_atrasados import fetch_reporte_premios_atrasados
from reports.reporte_premios_atrasados_mensaje import (
    build_detalle_local,
    build_mensaje_premios_atrasados,
)
from utils.logger import setup_logging

logger = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4096


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Programación vigente de premios Gane Premios por país.")
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="Muestra en consola el detalle disponible para auditoría; no lo envía.",
    )
    return parser.parse_args(argv)


def _split_telegram_messages(text):
    """Divide solo entre líneas; rechaza una línea individual demasiado larga."""
    messages = []
    current = ""
    for line in text.splitlines():
        if len(line) > TELEGRAM_LIMIT:
            raise ValueError(
                "Una línea del reporte excede el límite de Telegram y no puede "
                "enviarse sin partirla."
            )
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def _enviar(texto):
    try:
        messages = _split_telegram_messages(texto)
    except ValueError as exc:
        logger.error("No se pudo preparar el envío a Telegram: %s", exc)
        print(f"Error al enviar a Telegram: {exc}", file=sys.stderr)
        return 1

    sent = 0
    try:
        for message in messages:
            send_telegram_text(config.notifications, message)
            sent += 1
    except TelegramError as exc:
        logger.error("Telegram: %s", exc)
        suffix = f" ({sent} mensajes enviados antes del error)" if sent else ""
        print(f"Error al enviar a Telegram{suffix}: {exc}", file=sys.stderr)
        return 1
    print(f"Reporte enviado a Telegram en {sent} mensaje(s).")
    return 0


def main(argv=None):
    args = _parse_args(argv)
    try:
        report = fetch_reporte_premios_atrasados(config.db_hn)
        text = build_mensaje_premios_atrasados(report)
    except Exception as exc:
        logger.error("No se pudo generar el reporte: %s", exc)
        print(
            f"Error: no se pudo generar el reporte; no se envió nada. ({exc})",
            file=sys.stderr,
        )
        return 1

    print(text)
    if args.detalle:
        print(build_detalle_local(report))
    try:
        response = input("\n¿Desea enviar este reporte a Telegram? (s/n): ")
    except EOFError:
        response = ""
    if response.strip().lower() != "s":
        print("Envío cancelado.")
        return 0
    return _enviar(text)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    sys.exit(main())
