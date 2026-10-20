"""
utils/db.py
Manejo de conexiones a PostgreSQL con reintentos y contexto.
"""
import logging
import time
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extensions
import pandas as pd

from config.settings import DatabaseConfig

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Error de base de datos con contexto."""


@contextmanager
def get_connection(db_cfg: DatabaseConfig, retries: int = 3) -> Generator:
    """Context manager que entrega una conexión con reintentos.

    Usa cursor estándar (sin RealDictCursor) para compatibilidad
    plena con pd.read_sql_query.
    """
    conn = None
    for attempt in range(1, retries + 1):
        try:
            # Sin cursor_factory=RealDictCursor — pandas lo necesita estándar
            conn = psycopg2.connect(db_cfg.dsn())
            conn.set_session(readonly=True, autocommit=True)
            yield conn
            return
        except psycopg2.OperationalError as exc:
            logger.warning("Intento %d/%d fallido (%s): %s", attempt, retries, db_cfg.name, exc)
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                raise DatabaseError(
                    f"No se pudo conectar a '{db_cfg.name}' tras {retries} intentos"
                ) from exc
        finally:
            if conn and not conn.closed:
                conn.close()


# Columnas numéricas conocidas — se fuerza conversión después de cada query
_NUMERIC_COLS = {
    "enviados", "abiertos", "veces_jugado", "correctas", "incorrectas",
    "usuarios_unicos", "entregados_hoy", "entregados_ayer", "entregados_total",
    "promedio_x_dia", "entregados", "cancelados", "stock_total", "ganados",
    "faltan_por_entregar", "tiendas", "prob", "coupon_id", "reward_id",
    "trivia_correcta", "id", "form_id", "customer_id", "tienda_id",
}


def _coerce_numerics(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte a numérico las columnas que deberían serlo."""
    for col in df.columns:
        if col in _NUMERIC_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def query_df(db_cfg: DatabaseConfig, sql: str, params: tuple | None = None) -> pd.DataFrame:
    """
    Ejecuta una consulta y retorna un DataFrame con tipos correctos.
    Devuelve DataFrame vacío si ocurre un error, para no detener todo el reporte.
    """
    try:
        with get_connection(db_cfg) as conn:
            df = pd.read_sql_query(sql, conn, params=params)
            return _coerce_numerics(df)
    except DatabaseError as exc:
        logger.error("Error ejecutando query en '%s': %s", db_cfg.name, exc)
        return pd.DataFrame()
    except Exception as exc:
        logger.error("Error inesperado en '%s': %s", db_cfg.name, exc, exc_info=True)
        return pd.DataFrame()