"""
utils/logger.py
Configuración centralizada de logging con rotación de archivos.
"""
import logging
import logging.handlers
import os
from datetime import datetime


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Consola
    console = logging.StreamHandler()
    console.setFormatter(fmt)

    # Archivo con rotación diaria, retención 30 días
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "pulse_reports.log"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Silenciar librerías muy verbosas
    for noisy in ("matplotlib", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)