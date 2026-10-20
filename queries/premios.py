"""
queries/premios.py
Consultas de cupones, premios y campañas GP (queries 06–11).
"""
import pandas as pd
from datetime import date
from config.settings import DatabaseConfig
from utils.db import query_df


def _ids(lst: list[int]) -> str:
    return ",".join(str(i) for i in lst)


# ── Query 06: Cupones entregados ──────────────────────────────────────────────

CUPONES_SQL = """
SELECT
    r.id,
    r.title              AS reward,
    cr."createdAt"       AS entregado,
    cr.customer,
    c.first_name,
    c.last_name,
    c.identification     AS dni,
    c.telephone_number,
    ci.email,
    s.id                 AS tienda_id,
    s.public_name        AS tienda
FROM rewards r
INNER JOIN customerrewards cr ON r.id = cr.reward
INNER JOIN customers c        ON c.id = cr.customer
LEFT JOIN contactinfo ci      ON ci.id = c."contactInfo"
LEFT JOIN stores s            ON s.id = cr.store
WHERE r.id IN ({ids})
  AND cr."createdAt" BETWEEN '{desde}' AND '{hasta}';
"""


def get_cupones(db: DatabaseConfig, reward_ids: list[int],
                desde: str, hasta: str) -> pd.DataFrame:
    sql = CUPONES_SQL.format(ids=_ids(reward_ids), desde=desde, hasta=hasta)
    return query_df(db, sql)


# ── Query 07: Estado de premios reclamados ────────────────────────────────────

RECLAMOS_SQL = """
SELECT
    rc.metadata->'claim_form'->>'full_name'                                               AS nombre_ganador,
    COALESCE(rc.metadata->'claim_form'->>'identification', rc.identification)             AS cedula,
    rc.service_type                                                                       AS tipo_servicio,
    COALESCE(rc.metadata->'claim_form'->>'contact_phone', rc.telephone)                   AS telefono,
    rc.email,
    rc.id                                                                                 AS id_transaccion,
    rc."createdAt" AT TIME ZONE 'America/Costa_Rica'                                      AS fecha_transaccion,
    p.internal_code                                                                       AS codigo_premio,
    p.display_name                                                                        AS premio,
    p.delivery_provider                                                                   AS proveedor,
    CASE
        WHEN (LOWER(TRIM(COALESCE(rc.metadata->>'delivery_mode',''))) = 'automatico'
              OR LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'type',''))) = 'telco_provision')
             AND LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'status',''))) = 'delivered'
        THEN 'Entregado automático'
        WHEN (LOWER(TRIM(COALESCE(rc.metadata->>'delivery_mode',''))) = 'automatico'
              OR LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'type',''))) = 'telco_provision')
             AND LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'status',''))) = 'error'
        THEN 'Error en entrega'
        WHEN (LOWER(TRIM(COALESCE(rc.metadata->>'delivery_mode',''))) = 'automatico'
              OR LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'type',''))) = 'telco_provision')
             AND LOWER(TRIM(COALESCE(rc.metadata->'fulfillment'->>'status',''))) = 'pending_configuration'
        THEN 'Pendiente de configuración'
        WHEN rc.redeemed_at IS NULL AND rc.expires_at IS NOT NULL AND NOW() > rc.expires_at
        THEN 'Expirado'
        WHEN rc.redeemed_at IS NOT NULL
        THEN 'Redimido'
        WHEN rc.claimed_at IS NOT NULL
             AND LOWER(TRIM(COALESCE(rc.metadata->>'delivery_mode',''))) = 'retiro'
             AND rc.available_for_pickup_at IS NOT NULL
             AND NOW() >= rc.available_for_pickup_at
        THEN 'Disponible para retiro'
        WHEN rc.claimed_at IS NOT NULL
             AND LOWER(TRIM(COALESCE(rc.metadata->>'delivery_mode',''))) = 'retiro'
             AND rc.available_for_pickup_at IS NOT NULL
        THEN 'En traslado'
        WHEN rc.claimed_at IS NOT NULL THEN 'Reclamado'
        WHEN rc.state = 1 THEN 'Reclamado'
        WHEN rc.state = 2 THEN 'Redimido'
        WHEN rc.state = 3 THEN 'Expirado'
        ELSE 'Pendiente'
    END AS estado_premio,
    rc.claim_code                                                                         AS codigo_retiro,
    rc.selected_pickup_point->>'label'                                                    AS agencia,
    rc.selected_pickup_point->>'location_code'                                            AS codigo_agencia,
    COALESCE(rc.selected_pickup_point->>'province', rc.metadata->'claim_form'->>'province') AS provincia,
    COALESCE(rc.selected_pickup_point->>'canton',   rc.metadata->'claim_form'->>'canton')   AS canton,
    rc.metadata->'claim_form'->>'district'                                                AS distrito,
    rc.metadata->'claim_form'->>'address'                                                 AS direccion
FROM responsive_roulette_prize_claims rc
INNER JOIN responsive_roulette_prizes p ON p.id = rc.digital_dynamic_prize;
"""


def get_reclamos(db: DatabaseConfig) -> pd.DataFrame:
    """
    Retorna reclamos de premios. Si la tabla responsive_roulette_prize_claims
    no existe en esta BD, registra un aviso y retorna DataFrame vacío
    (no detiene el reporte).
    """
    import logging
    import psycopg2
    _log = logging.getLogger(__name__)
    try:
        from utils.db import get_connection
        with get_connection(db) as conn:
            df = pd.read_sql_query(RECLAMOS_SQL, conn)
            return df
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "UndefinedTable" in msg:
            _log.warning(
                "get_reclamos [%s]: tabla 'responsive_roulette_prize_claims' "
                "no existe en esta BD — sección de reclamos omitida.",
                db.name,
            )
        else:
            _log.error("get_reclamos [%s]: %s", db.name, exc, exc_info=True)
        return pd.DataFrame()


# ── Query 08: Resumen entrega por campaña y fecha ─────────────────────────────

RESUMEN_CAMPANA_SQL = """
SELECT
    cr.reward,
    r.title                                   AS premio,
    COUNT(*)                                  AS entregados,
    COUNT(*) FILTER (WHERE cr.state = -1)     AS cancelados,
    MIN(cr."date")                            AS primera_entrega,
    MAX(cr."date")                            AS ultima_entrega
FROM customerrewards cr
INNER JOIN transactions t ON t.id = cr."transactionId"
LEFT JOIN rewards r ON r.id = cr.reward
WHERE t.transaction_reference IN ({campaign_ids})
  AND t.transaction_reference_table = 'campaigns'
  AND cr."date" >= DATE '{fecha}'
  AND cr."date" <  DATE '{fecha_next}'
GROUP BY cr.reward, r.title
ORDER BY entregados DESC;
"""


def get_resumen_campana(db: DatabaseConfig, campaign_ids: list[int],
                        fecha: date) -> pd.DataFrame:
    sql = RESUMEN_CAMPANA_SQL.format(
        campaign_ids=_ids(campaign_ids),
        fecha=fecha.isoformat(),
        fecha_next=fecha.replace(day=fecha.day + 1).isoformat(),
    )
    return query_df(db, sql)


# ── Query 09: Premios entregados hoy ──────────────────────────────────────────

PREMIOS_HOY_SQL = """
SELECT
    cr.reward AS reward_id,
    r.title   AS premio,
    COUNT(*)  AS entregados_hoy,
    (NOW() AT TIME ZONE 'America/Tegucigalpa')::DATE AS fecha
FROM customerrewards cr
JOIN rewards r ON r.id = cr.reward
WHERE cr.reward IN ({ids})
  AND (cr."createdAt" AT TIME ZONE 'America/Tegucigalpa')::DATE
    = (NOW()           AT TIME ZONE 'America/Tegucigalpa')::DATE
GROUP BY cr.reward, r.title
ORDER BY entregados_hoy DESC;
"""


def get_premios_hoy(db: DatabaseConfig, all_reward_ids: list[int]) -> pd.DataFrame:
    sql = PREMIOS_HOY_SQL.format(ids=_ids(all_reward_ids))
    return query_df(db, sql)


# ── Query 10: Histórico y promedio ────────────────────────────────────────────

HISTORICO_SQL = """
SELECT
    r.title AS premio,
    COUNT(*) FILTER (
        WHERE (cr."createdAt" AT TIME ZONE 'America/Tegucigalpa')::date
            = (NOW()           AT TIME ZONE 'America/Tegucigalpa')::date - 1
    ) AS entregados_ayer,
    COUNT(*) AS entregados_total,
    ROUND(
        COUNT(*)::numeric /
        NULLIF(GREATEST(
            ((NOW() AT TIME ZONE 'America/Tegucigalpa')::date - DATE '2026-04-17'), 1
        ), 0),
    2) AS promedio_x_dia
FROM customerrewards cr
JOIN rewards r ON r.id = cr.reward
WHERE cr.reward IN ({ids})
GROUP BY r.title
ORDER BY entregados_total DESC;
"""


def get_historico(db: DatabaseConfig, all_reward_ids: list[int]) -> pd.DataFrame:
    sql = HISTORICO_SQL.format(ids=_ids(all_reward_ids))
    return query_df(db, sql)


# ── Query 11: Configuración GP ────────────────────────────────────────────────

CONFIG_GP_SQL = """
SELECT
    c.id                                                                AS campaign_id,
    c.name                                                              AS campana,
    m.commercial_name                                                   AS comercio,
    NULLIF(cup->>'couponId','')::INT                                     AS coupon_id,
    r.title                                                             AS premio,
    cup->>'winImg'                                                      AS imagen,
    CASE WHEN (cup->>'isWinner') IN ('true','1') THEN 'ganador' ELSE 'perdedor' END AS tipo,
    COALESCE((cup->>'isDefault') = 'true', FALSE)                       AS es_default,
    COALESCE((cup->>'prob')::NUMERIC, 0)                                AS prob,
    CASE
        WHEN (cup->'storeConfig'->>'isGlobal') = 'true' THEN 'global'
        ELSE 'por tienda'
    END AS alcance,
    CASE
        WHEN (cup->'storeConfig'->>'isGlobal') = 'true'
        THEN COALESCE((cup->>'qty')::INT, 0)
        ELSE COALESCE(sq.stock_tiendas, 0)
    END AS stock_total,
    COALESCE((cup->>'won')::INT, 0) AS ganados,
    (CASE
        WHEN (cup->'storeConfig'->>'isGlobal') = 'true'
        THEN COALESCE((cup->>'qty')::INT, 0)
        ELSE COALESCE(sq.stock_tiendas, 0)
    END) - COALESCE((cup->>'won')::INT, 0) AS faltan_por_entregar,
    COALESCE(JSONB_ARRAY_LENGTH(cup->'storeConfig'->'stores'), 0) AS tiendas
FROM campaigns c
INNER JOIN merchants m ON m.id = c.merchant
CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(c.push_data::JSONB->0->'gpCoupons') AS cup
LEFT JOIN rewards r ON r.id = NULLIF(cup->>'couponId','')::INT
LEFT JOIN LATERAL (
    SELECT SUM(NULLIF(value,'')::INT) AS stock_tiendas
    FROM JSONB_EACH_TEXT(cup->'storeQty')
) sq ON TRUE
WHERE c.push_type = 'gp'
  AND c.status = 1
  AND c.begin_date < CURRENT_DATE + 1
  AND c.end_date   >= CURRENT_DATE
ORDER BY m.commercial_name, c.name, tipo DESC, coupon_id;
"""


def get_config_gp(db: DatabaseConfig) -> pd.DataFrame:
    return query_df(db, CONFIG_GP_SQL)