"""
scheduler.py
Programa la ejecución del reporte según la configuración.
Modo por intervalo (cada N minutos) + reporte diario a hora fija.
"""
import logging
import signal
import sys
import time

import schedule

from config.settings import config
from main import run_report
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _safe_run() -> None:
    """Ejecuta el reporte capturando cualquier excepción para no detener el scheduler."""
    try:
        run_report(config)
    except Exception as exc:
        logger.error("Error en ejecución programada: %s", exc, exc_info=True)


def _setup_schedule() -> None:
    interval = config.scheduler.interval_minutes
    daily_time = config.scheduler.daily_report_time

    # Reporte periódico (p. ej. cada 60 min)
    schedule.every(interval).minutes.do(_safe_run)
    logger.info("Reporte periódico: cada %d minutos.", interval)

    # Reporte diario a hora fija (resumen del día)
    schedule.every().day.at(daily_time).do(_safe_run)
    logger.info("Reporte diario a las %s.", daily_time)


def _handle_signal(signum, frame) -> None:
    logger.info("Señal %d recibida — apagando scheduler.", signum)
    sys.exit(0)


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Pulse Reports Scheduler iniciado.")

    # Ejecutar inmediatamente al arrancar
    _safe_run()
    _setup_schedule()

    logger.info("Esperando próxima ejecución programada…")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()