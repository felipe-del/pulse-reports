"""Reporte diario de premios desglosado por campaña (solo lectura, sin Telegram)."""
import logging
import sys

from config.settings import config
from queries.reporte_diario_premios_campania import fetch_reporte_por_campania
from reports.reporte_diario_premios_campania_mensaje import build_mensaje_por_campania


def main() -> int:
    try:
        report = fetch_reporte_por_campania(config.db_hn)
        print(build_mensaje_por_campania(report))
        return 0
    except Exception as exc:
        logging.getLogger(__name__).exception("No se pudo generar el reporte por campaña.")
        print(f"Error: no se pudo generar el reporte. ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
