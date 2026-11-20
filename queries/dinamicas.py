"""Consultas de solo lectura para los reportes de dinamicas promocionales."""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from config.settings import DatabaseConfig
from config.dinamicas import Dynamics
from utils.db import get_connection

logger = logging.getLogger(__name__)


class DynamicsQueryError(RuntimeError):
    """Fallo de consulta con fuente identificada y sin datos personales."""


SOURCE_REQUIREMENTS = {
    "historias": {
        "storiescustomers": {"id", "story", "customer"},
        "story_actions": {"story", "customer", "description"},
    },
    "raspaditas": {
        "campaigncustomer": {"id", "campaign", "customer", "is_scratch_push_winner", "createdAt"},
        "customers": {"id", "first_name", "last_name", "telephone_number", "contactInfo"},
        "contactinfo": {"id", "email"},
    },
    "retos": {
        "challenges": {"id", "name"},
        "challengecustomer": {"id", "challenge", "customer", "createdAt"},
        "challengecustomerrecords": {"id", "challenge", "customer", "createdAt"},
        "customers": {"id", "first_name", "last_name", "telephone_number", "contactInfo"},
        "contactinfo": {"id", "email"},
    },
    "cupones": {
        "rewards": {"id", "title"},
        "customerrewards": {"id", "reward", "customer", "createdAt", "state"},
        "customers": {"id", "first_name", "last_name", "telephone_number", "contactInfo"},
        "contactinfo": {"id", "email"},
    },
}

SCHEMA_SQL = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = ANY(%s)
ORDER BY table_name, ordinal_position;
"""

_TABLES = sorted({table for source in SOURCE_REQUIREMENTS.values() for table in source})
_PARENT_ID_TABLES = {
    "historia_id": ("stories", "id"),
    "campania_raspadita_id": ("campaigns", "id"),
    "reto_id": ("challenges", "id"),
    "cupon_id": ("rewards", "id"),
}
_SOURCE_BY_ID = {
    "historia_id": "historias",
    "campania_raspadita_id": "raspaditas",
    "reto_id": "retos",
    "cupon_id": "cupones",
}


@dataclass
class DynamicsData:
    dinamica: Dynamics
    generated_at: datetime
    periodo_desde: Optional[date]
    periodo_hasta: Optional[date]
    story_summary: pd.DataFrame
    story_action_descriptions: dict[int, set[str]]
    scratch: pd.DataFrame
    challenges: pd.DataFrame
    coupons: pd.DataFrame
    source_status: dict[str, str]
    schema: dict[str, dict[str, str]]


def _read(conn, sql: str, params: tuple = ()) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [column[0] for column in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def _run_source(source: str, function, *args):
    try:
        return function(*args)
    except Exception as exc:
        diagnostics = getattr(exc, "diag", None)
        technical_message = getattr(diagnostics, "message_primary", None) or str(exc)
        logger.error("Consulta de la fuente %s fallida (%s): %s",
                     source, type(exc).__name__, technical_message)
        raise DynamicsQueryError(
            f"Consulta de la fuente {source} fallida ({type(exc).__name__}): "
            f"{technical_message}") from exc


def inspect_schema(conn) -> dict[str, dict[str, str]]:
    rows = _read(conn, SCHEMA_SQL, (_TABLES + ["stories", "campaigns"],))
    schema: dict[str, dict[str, str]] = {}
    for row in rows.itertuples(index=False):
        schema.setdefault(row.table_name, {})[row.column_name] = row.data_type
    return schema


def source_compatibility(schema: dict[str, dict[str, str]]) -> dict[str, tuple[bool, str]]:
    result = {}
    for source, requirements in SOURCE_REQUIREMENTS.items():
        missing = [
            f"{table}.{column}"
            for table, columns in requirements.items()
            for column in sorted(columns - schema.get(table, {}).keys())
        ]
        result[source] = (
            not missing,
            "Esquema compatible" if not missing else "Faltan: " + ", ".join(missing),
        )
    return result


def _configured_id_status(conn, schema: dict[str, dict[str, str]],
                          dynamics: Dynamics) -> tuple[dict[str, str], dict[str, str], list[str]]:
    source_errors, unverified, messages = {}, {}, []
    for key, (table, column) in _PARENT_ID_TABLES.items():
        ids = sorted(set(_all_ids(dynamics.variantes, key)))
        if column not in schema.get(table, {}):
            message = f"ID no verificable: no existe {table}.{column} en el esquema visible"
            messages.append(f"{key}: {message}")
            unverified[_SOURCE_BY_ID[key]] = message
            continue
        found = _read(
            conn, f"SELECT {column} FROM {table} WHERE {column} = ANY(%s);", (ids,))
        present = {int(value) for value in found[column].dropna()}
        missing = sorted(set(ids) - present)
        messages.append(
            f"{key}: {len(present)}/{len(ids)} IDs encontrados"
            + (f"; faltan {missing}" if missing else ""))
        if missing:
            source_errors[_SOURCE_BY_ID[key]] = f"IDs configurados no encontrados: {missing}"
    return source_errors, unverified, messages


def _period_clause(alias: str, desde: Optional[date], hasta: Optional[date],
                   tz: str, timestamp_type: str) -> tuple[str, dict]:
    clauses, params = [], {}
    aware = timestamp_type == "timestamp with time zone"

    def boundary(day: date) -> datetime:
        value = datetime.combine(day, time.min, tzinfo=ZoneInfo(tz))
        return value if aware else value.replace(tzinfo=None)

    if desde:
        clauses.append(f"{alias} >= %(desde)s")
        params["desde"] = boundary(desde)
    if hasta:
        clauses.append(f"{alias} < %(hasta)s")
        params["hasta"] = boundary(hasta + timedelta(days=1))
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def _all_ids(variants: list[dict], key: str) -> list[int]:
    return [int(variant[key]) for variant in variants]


def _query_stories(conn, dynamics: Dynamics) -> pd.DataFrame:
    values, params = [], {}
    for index, variant in enumerate(dynamics.variantes):
        values.append(
            f"(%(story{index})s::int, %(views{index})s::text[], %(cta{index})s::text[])")
        params[f"story{index}"] = variant["historia_id"]
        params[f"views{index}"] = variant.get("eventos_vista")
        params[f"cta{index}"] = variant.get("eventos_cta") or []
    sql = """
    WITH configured(story_id, view_events, cta_events) AS (
        VALUES {values}
    )
    , assigned_actions AS (
        SELECT cfg.story_id, cfg.view_events, cfg.cta_events,
               sc.id AS story_customer_id, sc.customer AS assigned_customer,
               sa.customer AS action_customer, sa.description
        FROM configured cfg
        LEFT JOIN storiescustomers sc ON sc.story = cfg.story_id
        LEFT JOIN story_actions sa ON sa.story = sc.id
    ),
    per_story AS (
        SELECT story_id,
               COUNT(DISTINCT story_customer_id) AS historias_asignadas,
               COUNT(DISTINCT assigned_customer) AS usuarios_asignados,
               COUNT(action_customer) FILTER (
                   WHERE view_events IS NULL OR description = ANY(view_events)
               ) AS total_vistas,
               COUNT(DISTINCT action_customer) FILTER (
                   WHERE view_events IS NULL OR description = ANY(view_events)
               ) AS usuarios_unicos_vistas,
               COUNT(action_customer) FILTER (
                   WHERE description = ANY(cta_events)
               ) AS total_cta,
               COUNT(DISTINCT action_customer) FILTER (
                   WHERE description = ANY(cta_events)
               ) AS usuarios_cta,
               COUNT(action_customer) AS acciones_registradas
        FROM assigned_actions
        GROUP BY story_id, view_events, cta_events
    ),
    total AS (
        SELECT COUNT(DISTINCT story_customer_id) AS historias_asignadas,
               COUNT(DISTINCT assigned_customer) AS usuarios_asignados,
               COUNT(action_customer) FILTER (
                   WHERE view_events IS NULL OR description = ANY(view_events)
               ) AS total_vistas,
               COUNT(DISTINCT action_customer) FILTER (
                   WHERE view_events IS NULL OR description = ANY(view_events)
               ) AS usuarios_unicos_vistas,
               COUNT(action_customer) FILTER (
                   WHERE description = ANY(cta_events)
               ) AS total_cta,
               COUNT(DISTINCT action_customer) FILTER (
                   WHERE description = ANY(cta_events)
               ) AS usuarios_cta,
               COUNT(action_customer) AS acciones_registradas
        FROM assigned_actions
    )
    SELECT story_id AS historia_id, 0 AS es_total, historias_asignadas,
           usuarios_asignados, total_vistas, usuarios_unicos_vistas,
           total_cta, usuarios_cta, acciones_registradas
    FROM per_story
    UNION ALL
    SELECT NULL::int AS historia_id, 1 AS es_total, historias_asignadas,
           usuarios_asignados, total_vistas, usuarios_unicos_vistas,
           total_cta, usuarios_cta, acciones_registradas
    FROM total;
    """
    return _read(conn, sql.format(values=", ".join(values)), params)


def _query_story_descriptions(conn, dynamics: Dynamics) -> dict[int, set[str]]:
    result = _read(conn, """
        SELECT DISTINCT sc.story AS historia_id, sa.description
        FROM story_actions sa
        JOIN storiescustomers sc ON sc.id = sa.story
        WHERE sc.story = ANY(%s)
          AND sa.description IS NOT NULL
        ORDER BY sc.story, sa.description;
    """, (_all_ids(dynamics.variantes, "historia_id"),))
    descriptions = {}
    for row in result.itertuples(index=False):
        descriptions.setdefault(int(row.historia_id), set()).add(str(row.description))
    return descriptions


def _query_scratch(conn, dynamics: Dynamics, desde: date | None, hasta: date | None,
                   timestamp_type: str) -> pd.DataFrame:
    sql = """
    SELECT cc.id AS registro_id, cc.customer AS cliente_id,
           cc.campaign AS campania_id, cc.is_scratch_push_winner AS resultado,
           cc."createdAt" AS fecha_registro,
           c.first_name AS nombre, c.last_name AS apellido, ci.email AS correo,
           c.telephone_number AS telefono
    FROM campaigncustomer cc
    LEFT JOIN customers c ON c.id = cc.customer
    LEFT JOIN contactinfo ci ON ci.id = c."contactInfo"
    WHERE cc.campaign = ANY(%(campaign_ids)s)
      {period}
    ORDER BY cc."createdAt", cc.id;
    """
    period, period_params = _period_clause(
        'cc."createdAt"', desde, hasta, dynamics.zona_horaria,
        timestamp_type)
    return _read(conn, sql.format(period=period), {
        "campaign_ids": _all_ids(dynamics.variantes, "campania_raspadita_id"),
        **period_params,
    })


def _query_challenges(conn, dynamics: Dynamics, desde: date | None,
                      hasta: date | None, timestamp_type: str) -> pd.DataFrame:
    completion_period, completion_params = _period_clause(
        '"createdAt"', None, hasta, dynamics.zona_horaria, timestamp_type)
    sql = """
    WITH cumplimiento AS (
        SELECT challenge, customer, COUNT(*) AS cantidad_registros,
               MIN("createdAt") AS fecha_cumplimiento
        FROM challengecustomerrecords
        WHERE challenge = ANY(%(challenge_ids)s)
          {completion_period}
        GROUP BY challenge, customer
    )
    SELECT ch.id AS reto_id, ch.name AS nombre_reto,
           cc.id AS aceptacion_id, cc.customer AS cliente_id,
           cc."createdAt" AS fecha_aceptacion,
           c.first_name AS nombre, c.last_name AS apellido,
           ci.email AS correo,
           COALESCE(cum.cantidad_registros, 0) AS cantidad_registros_cumplimiento,
           cum.fecha_cumplimiento,
           (cum.cantidad_registros IS NOT NULL) AS completado
    FROM challengecustomer cc
    JOIN challenges ch ON ch.id = cc.challenge
    LEFT JOIN customers c ON c.id = cc.customer
    LEFT JOIN contactinfo ci ON ci.id = c."contactInfo"
    LEFT JOIN cumplimiento cum
      ON cum.challenge = cc.challenge AND cum.customer = cc.customer
    WHERE cc.challenge = ANY(%(challenge_ids)s)
      {period}
    ORDER BY cc."createdAt", cc.id;
    """
    period, period_params = _period_clause(
        'cc."createdAt"', desde, hasta, dynamics.zona_horaria,
        timestamp_type)
    return _read(conn, sql.format(period=period, completion_period=completion_period), {
        "challenge_ids": _all_ids(dynamics.variantes, "reto_id"),
        **period_params,
        **completion_params,
    })


def _query_coupons(conn, dynamics: Dynamics, desde: date | None,
                   hasta: date | None, timestamp_type: str) -> pd.DataFrame:
    sql = """
    SELECT cr.id AS asignacion_id, cr.customer AS cliente_id,
           cr.reward AS cupon_id, r.title AS nombre_cupon,
           cr."createdAt" AS fecha_asignacion, cr.state AS estado,
           c.first_name AS nombre, c.last_name AS apellido,
           ci.email AS correo, c.telephone_number AS telefono
    FROM customerrewards cr
    JOIN rewards r ON r.id = cr.reward
    LEFT JOIN customers c ON c.id = cr.customer
    LEFT JOIN contactinfo ci ON ci.id = c."contactInfo"
    WHERE cr.reward = ANY(%(reward_ids)s)
      {period}
    ORDER BY cr."createdAt", cr.id;
    """
    period, period_params = _period_clause(
        'cr."createdAt"', desde, hasta, dynamics.zona_horaria,
        timestamp_type)
    return _read(conn, sql.format(period=period), {
        "reward_ids": _all_ids(dynamics.variantes, "cupon_id"),
        **period_params,
    })


def fetch_dynamics(db: DatabaseConfig, dynamics: Dynamics, generated_at: datetime,
                   desde: date | None = None, hasta: date | None = None) -> DynamicsData:
    """Obtiene fuentes compatibles. Cualquier error de consulta aborta el reporte."""
    with get_connection(db) as conn:
        schema = _run_source("esquema PostgreSQL", inspect_schema, conn)
        compatibility = source_compatibility(schema)
        source_status = {name: message for name, (_, message) in compatibility.items()}
        id_errors, id_unverified, _ = _run_source(
            "IDs configurados", _configured_id_status, conn, schema, dynamics)

        story = pd.DataFrame()
        story_descriptions = {}
        scratch = pd.DataFrame()
        challenges = pd.DataFrame()
        coupons = pd.DataFrame()
        story_period_unavailable = bool(desde or hasta) and compatibility["historias"][0]
        if (compatibility["historias"][0] and not story_period_unavailable
                and "historias" not in id_errors
                and "historias" not in id_unverified):
            story = _run_source("historias", _query_stories, conn, dynamics)
            story_descriptions = _run_source(
                "descripciones de historias", _query_story_descriptions, conn, dynamics)
            source_status["historias"] = "Disponible"
        elif "historias" in id_errors:
            source_status["historias"] = id_errors["historias"]
        elif "historias" in id_unverified:
            source_status["historias"] = id_unverified["historias"]
        elif story_period_unavailable:
            source_status["historias"] = (
                "No disponible para el periodo: la fuente no tiene una fecha validada")
        if (compatibility["raspaditas"][0] and "raspaditas" not in id_errors
                and "raspaditas" not in id_unverified):
            scratch = _run_source("raspaditas", _query_scratch,
                conn, dynamics, desde, hasta,
                schema["campaigncustomer"]["createdAt"])
            source_status["raspaditas"] = "Disponible"
        elif "raspaditas" in id_errors:
            source_status["raspaditas"] = id_errors["raspaditas"]
        elif "raspaditas" in id_unverified:
            source_status["raspaditas"] = id_unverified["raspaditas"]
        if (compatibility["retos"][0] and "retos" not in id_errors
                and "retos" not in id_unverified):
            challenges = _run_source("retos", _query_challenges,
                conn, dynamics, desde, hasta,
                schema["challengecustomer"]["createdAt"])
            source_status["retos"] = "Disponible"
        elif "retos" in id_errors:
            source_status["retos"] = id_errors["retos"]
        elif "retos" in id_unverified:
            source_status["retos"] = id_unverified["retos"]
        if (compatibility["cupones"][0] and "cupones" not in id_errors
                and "cupones" not in id_unverified):
            coupons = _run_source("cupones", _query_coupons,
                conn, dynamics, desde, hasta,
                schema["customerrewards"]["createdAt"])
            source_status["cupones"] = "Disponible"
        elif "cupones" in id_errors:
            source_status["cupones"] = id_errors["cupones"]
        elif "cupones" in id_unverified:
            source_status["cupones"] = id_unverified["cupones"]

    return DynamicsData(
        dinamica=dynamics,
        generated_at=generated_at,
        periodo_desde=desde,
        periodo_hasta=hasta,
        story_summary=story,
        story_action_descriptions=story_descriptions,
        scratch=scratch,
        challenges=challenges,
        coupons=coupons,
        source_status=source_status,
        schema=schema,
    )


def check_connection(db: DatabaseConfig, dynamics: Dynamics) -> tuple[dict, dict, list[str]]:
    """Verifica SELECT, esquema y existencia de IDs cuando hay tabla padre conocida."""
    with get_connection(db) as conn:
        ok = _read(conn, "SELECT 1 AS ok;")
        if int(ok.iloc[0]["ok"]) != 1:
            raise RuntimeError("SELECT 1 devolvió un valor inesperado.")
        schema = _run_source("esquema PostgreSQL", inspect_schema, conn)
        compatibility = source_compatibility(schema)
        _, _, id_status = _run_source(
            "IDs configurados", _configured_id_status, conn, schema, dynamics)
        descriptions = []
        if compatibility["historias"][0]:
            descriptions = _run_source(
                "descripciones de historias", _query_story_descriptions, conn, dynamics)
            descriptions = [
                f"historia {story_id}: {sorted(values)!r}"
                for story_id, values in sorted(descriptions.items())
            ]
    return schema, {name: f"{'OK' if ok else 'NO'} — {detail}"
                    for name, (ok, detail) in compatibility.items()}, id_status + [
                        "story_actions.description: " + (
                            "; ".join(descriptions) if descriptions else "sin valores")
                    ]
