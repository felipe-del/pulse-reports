"""Consulta SELECT del reporte diario de premios desglosado por campaña."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import DatabaseConfig
from queries.reporte_diario_premios import (
    CAMPANIAS,
    PAISES,
    TZ,
    _CAMPAIGNS_SQL,
    _CREATED_DATE,
    _CREATED_LE_CORTE,
    _CREATED_TYPE_SQL,
    _schedule_totals,
    _unique_campaign_configs,
)
from utils.db import get_connection


CAMPAIGN_ROWS = tuple(CAMPANIAS)
CAMPAIGN_IDS = tuple(campaign_id for campaign_id, _ in CAMPAIGN_ROWS)

CAMPAIGN_REPORT_SQL = """
WITH campanias(campaign_id, pais) AS (
    VALUES
        (818, 'Guatemala'),
        (819, 'Guatemala'),
        (820, 'Guatemala'),
        (825, 'El Salvador'),
        (826, 'El Salvador'),
        (828, 'Honduras'),
        (829, 'Honduras')
),
corte AS (
    SELECT
        instante.corte AS fecha_hora_corte,
        (instante.corte AT TIME ZONE 'America/Guatemala')::date AS fecha_hoy,
        (instante.corte AT TIME ZONE 'America/Guatemala')::time AS hora_corte
    FROM (SELECT NOW() AS corte) instante
),
adjudicados_hoy AS (
    SELECT
        cpw."campaignId" AS campaign_id,
        SUM(cpw."totalPrizes") AS cantidad
    FROM "CustomerPrizeWins" cpw
    CROSS JOIN corte ct
    WHERE cpw."campaignId" IN (818, 819, 820, 825, 826, 828, 829)
      AND {created_date} = ct.fecha_hoy
      AND {created_le_corte}
    GROUP BY cpw."campaignId"
),
adjudicados_acumulados AS (
    SELECT
        cpw."campaignId" AS campaign_id,
        SUM(cpw."totalPrizes") AS cantidad
    FROM "CustomerPrizeWins" cpw
    CROSS JOIN corte ct
    WHERE cpw."campaignId" IN (818, 819, 820, 825, 826, 828, 829)
      AND {created_le_corte}
    GROUP BY cpw."campaignId"
)
SELECT
    ca.campaign_id,
    ca.pais,
    ct.fecha_hora_corte,
    ct.fecha_hoy,
    TO_CHAR(ct.hora_corte, 'HH12:MI AM') AS hora_corte,
    COALESCE(ah.cantidad, 0) AS adjudicados_hoy_al_corte,
    COALESCE(aa.cantidad, 0) AS adjudicados_acumulados_al_corte
FROM campanias ca
CROSS JOIN corte ct
LEFT JOIN adjudicados_hoy ah ON ah.campaign_id = ca.campaign_id
LEFT JOIN adjudicados_acumulados aa ON aa.campaign_id = ca.campaign_id
ORDER BY CASE ca.pais
    WHEN 'Guatemala' THEN 1
    WHEN 'El Salvador' THEN 2
    WHEN 'Honduras' THEN 3
END, ca.campaign_id;
"""


def _sum_or_missing(values):
    if any(value is None for value in values):
        return None
    return sum(values)


def aggregate_campaign_rows(campaigns):
    """Construye totales de país propagando cualquier métrica ausente."""
    countries = []
    for country in PAISES:
        rows = [row for row in campaigns if row["pais"] == country]
        if not rows:
            raise RuntimeError(f"No hay campañas definidas para {country}.")
        country_total = {"pais": country}
        for field in (
            "programado_hoy_config_vigente",
            "programados_nuevos_hoy",
            "programados_arrastrados_hoy",
            "programados_vigentes_fin_posterior_hoy",
            "programado_acumulado_config_vigente",
            "adjudicados_hoy_al_corte",
            "adjudicados_acumulados_al_corte",
        ):
            country_total[field] = _sum_or_missing([row[field] for row in rows])
        rollover = {}
        if any(row["arrastrados_por_fecha_inicio"] is None for row in rows):
            country_total["arrastrados_por_fecha_inicio"] = None
        else:
            for row in rows:
                for start_date, quantity in row["arrastrados_por_fecha_inicio"].items():
                    rollover[start_date] = rollover.get(start_date, 0) + quantity
            country_total["arrastrados_por_fecha_inicio"] = rollover

        for field in (
            "programado_hoy_config_vigente",
            "programados_nuevos_hoy",
            "programados_arrastrados_hoy",
            "programados_vigentes_fin_posterior_hoy",
            "programado_acumulado_config_vigente",
            "adjudicados_hoy_al_corte",
            "adjudicados_acumulados_al_corte",
        ):
            values = [row[field] for row in rows]
            if country_total[field] is not None and sum(values) != country_total[field]:
                raise RuntimeError(f"La suma de campañas no coincide para {country}, {field}.")
        countries.append(country_total)
    return countries


def fetch_reporte_por_campania(db: DatabaseConfig):
    """Obtiene campañas, adjudicaciones y programación vigente con un único corte SQL."""
    with get_connection(db) as conn:
        with conn.cursor() as cursor:
            cursor.execute(_CREATED_TYPE_SQL)
            type_row = cursor.fetchone()
        if not type_row or type_row[0] not in _CREATED_DATE:
            raise RuntimeError('No se pudo determinar el tipo de "CustomerPrizeWins"."createdAt".')

        with conn.cursor() as cursor:
            cursor.execute(_CAMPAIGNS_SQL, (list(CAMPAIGN_IDS),))
            config_rows = cursor.fetchall()

        sql = CAMPAIGN_REPORT_SQL.format(
            created_date=_CREATED_DATE[type_row[0]],
            created_le_corte=_CREATED_LE_CORTE[type_row[0]],
        )
        with conn.cursor() as cursor:
            cursor.execute(sql)
            result_rows = cursor.fetchall()

    if len(result_rows) != len(CAMPAIGN_ROWS):
        raise RuntimeError(
            f"Resultado incompleto: se esperaban {len(CAMPAIGN_ROWS)} campañas, "
            f"se recibieron {len(result_rows)}.")
    if tuple(row[0] for row in result_rows) != CAMPAIGN_IDS:
        raise RuntimeError("Resultado incompleto: identificadores de campaña inesperados.")

    configs = _unique_campaign_configs(config_rows)
    for campaign_id in CAMPAIGN_IDS:
        if campaign_id not in configs:
            configs[campaign_id] = None
    cutoff_values = {row[2] for row in result_rows}
    if len(cutoff_values) != 1:
        raise RuntimeError("Las campañas recibieron instantes de corte distintos.")
    cutoff = next(iter(cutoff_values))
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
        raise RuntimeError("El corte único no es un timestamp con zona.")
    local_cut = cutoff.astimezone(ZoneInfo(TZ))
    schedule = _schedule_totals(configs, local_cut.date())

    campaigns = []
    for row in result_rows:
        campaign_id, country, _, report_date, cutoff_time, wins_today, wins_all = row
        configured = schedule[int(campaign_id)]
        campaigns.append({
            "campaign_id": int(campaign_id),
            "pais": country,
            "fecha": report_date,
            "hora_corte": cutoff_time,
            **configured,
            "adjudicados_hoy_al_corte": int(wins_today),
            "adjudicados_acumulados_al_corte": int(wins_all),
        })

    countries = aggregate_campaign_rows(campaigns)
    return {
        "fecha_hora_corte": cutoff,
        "campanias": campaigns,
        "paises": countries,
    }
