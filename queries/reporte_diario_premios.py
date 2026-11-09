"""
queries/reporte_diario_premios.py
Consulta (solo SELECT) del reporte diario Gane Premios por país.
"""
import json
import logging
from datetime import date, time

import pandas as pd

from config.settings import DatabaseConfig
from utils.db import get_connection

logger = logging.getLogger(__name__)

TZ = "America/Guatemala"
PAISES = ("Guatemala", "El Salvador", "Honduras")
CAMPANIAS = (
    (818, "Guatemala"), (819, "Guatemala"), (820, "Guatemala"),
    (825, "El Salvador"), (826, "El Salvador"),
    (828, "Honduras"), (829, "Honduras"),
)

_CAMPAIGNS_SQL = """
SELECT id, push_data
FROM campaigns
WHERE id = ANY(%s)
ORDER BY id;
"""

_CREATED_TYPE_SQL = """
SELECT data_type
FROM information_schema.columns
WHERE table_name = 'CustomerPrizeWins' AND column_name = 'createdAt'
LIMIT 1;
"""

# createdAt con zona (timestamptz): se convierte a hora de Guatemala y se compara
# contra el instante de corte. Sin zona (timestamp): ya se trata como hora local
# (igual que los demás reportes), por lo que no se aplica AT TIME ZONE.
_CREATED_DATE = {
    "timestamp with time zone": '(cpw."createdAt" AT TIME ZONE \'America/Guatemala\')::date',
    "timestamp without time zone": 'cpw."createdAt"::date',
}
_CREATED_LE_CORTE = {
    "timestamp with time zone": 'cpw."createdAt" <= ct.fecha_hora_corte',
    "timestamp without time zone":
        'cpw."createdAt" <= (ct.fecha_hora_corte AT TIME ZONE \'America/Guatemala\')',
}

REPORT_SQL = """
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
campaign_configs(campaign_id, payload) AS (
    VALUES
        {campaign_configs}
),
corte AS (
    SELECT
        NOW() AS fecha_hora_corte,
        (NOW() AT TIME ZONE 'America/Guatemala')::date
            AS fecha_hoy,
        (NOW() AT TIME ZONE 'America/Guatemala')::time
            AS hora_corte
),
horarios AS (
    SELECT
        c.id AS campaign_id,
        (slot ->> 'dateFrom')::date AS fecha_programada,
        (slot ->> 'timeFrom')::time AS hora_habilitacion,
        COALESCE(NULLIF(slot ->> 'qty', '')::int, 0)
            AS cantidad_premios
    FROM campaigns c
    JOIN campanias ca
        ON ca.campaign_id = c.id
    JOIN campaign_configs cc
        ON cc.campaign_id = c.id
    CROSS JOIN LATERAL
        jsonb_array_elements(
            CASE WHEN jsonb_typeof(cc.payload) = 'array'
                 THEN cc.payload ELSE '[]'::jsonb END
        ) config
    CROSS JOIN LATERAL
        jsonb_array_elements(
            CASE WHEN jsonb_typeof(config -> 'gpCoupons') = 'array'
                 THEN config -> 'gpCoupons' ELSE '[]'::jsonb END
        ) cupon
    CROSS JOIN LATERAL
        jsonb_array_elements(
            CASE WHEN jsonb_typeof(cupon -> 'storeConfig' -> 'stores') = 'array'
                 THEN cupon -> 'storeConfig' -> 'stores' ELSE '[]'::jsonb END
        ) tienda
    CROSS JOIN LATERAL
        jsonb_array_elements(
            CASE WHEN jsonb_typeof(tienda -> 'slots') = 'array'
                 THEN tienda -> 'slots' ELSE '[]'::jsonb END
        ) slot
    WHERE cupon -> 'isWinner' = 'true'::jsonb
),
programacion AS (
    SELECT
        h.campaign_id,
        SUM(h.cantidad_premios)
            AS premios_a_entregar_hoy,
        COALESCE(
            SUM(h.cantidad_premios) FILTER (
                WHERE h.hora_habilitacion <= ct.hora_corte
            ),
            0
        ) AS premios_habilitados_corte
    FROM horarios h
    CROSS JOIN corte ct
    WHERE h.fecha_programada = ct.fecha_hoy
    GROUP BY h.campaign_id
),
ganadores AS (
    SELECT
        cpw."campaignId" AS campaign_id,
        COALESCE(SUM(cpw."totalPrizes"), 0) AS premios_entregados_hoy
    FROM "CustomerPrizeWins" cpw
    CROSS JOIN corte ct
    WHERE cpw."campaignId" IN (
        818, 819, 820,
        825, 826,
        828, 829
    )
      AND {created_date} = ct.fecha_hoy
      AND {created_le_corte}
    GROUP BY cpw."campaignId"
),
ganadores_acumulado AS (
    SELECT
        cpw."campaignId" AS campaign_id,
        COALESCE(SUM(cpw."totalPrizes"), 0) AS total_entregados_acumulado
    FROM "CustomerPrizeWins" cpw
    CROSS JOIN corte ct
    WHERE cpw."campaignId" IN (
        818, 819, 820,
        825, 826,
        828, 829
    )
      AND {created_le_corte}
    GROUP BY cpw."campaignId"
)
SELECT
    ca.pais AS pais,
    ct.fecha_hoy AS fecha,
    TO_CHAR(ct.hora_corte, 'HH12:MI AM') AS hora_corte,

    SUM(COALESCE(pr.premios_a_entregar_hoy, 0))
        AS premios_a_entregar_total,

    SUM(COALESCE(pr.premios_habilitados_corte, 0))
        AS premios_a_entregar_corte,

    SUM(COALESCE(g.premios_entregados_hoy, 0))
        AS premios_entregados_hoy,

    SUM(COALESCE(ga.total_entregados_acumulado, 0))
        AS total_entregados_acumulados

FROM campanias ca
CROSS JOIN corte ct
LEFT JOIN programacion pr
    ON pr.campaign_id = ca.campaign_id
LEFT JOIN ganadores g
    ON g.campaign_id = ca.campaign_id
LEFT JOIN ganadores_acumulado ga
    ON ga.campaign_id = ca.campaign_id

GROUP BY ca.pais, ct.fecha_hoy, ct.hora_corte

ORDER BY CASE ca.pais
    WHEN 'Guatemala' THEN 1
    WHEN 'El Salvador' THEN 2
    WHEN 'Honduras' THEN 3
END;
"""

NUMERIC_COLS = ("premios_a_entregar_total", "premios_a_entregar_corte", "premios_entregados_hoy",
                "total_entregados_acumulados")

class CampaignConfigurationError(RuntimeError):
    """La configuración de premios no permite generar un reporte confiable."""


def _configuration_warning(campaign_id: int, field: str, detail: str) -> str:
    message = f"Campaña {campaign_id}, campo {field}: {detail}"
    logger.warning("Configuración de premios inválida — %s", message)
    return message


def _decode_campaign_payload(campaign_id: int, raw_payload) -> list:
    field = "push_data"
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        if isinstance(payload, str):
            payload = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CampaignConfigurationError(
            _configuration_warning(campaign_id, field, f"JSON inválido ({exc}).")) from exc

    if not isinstance(payload, list):
        raise CampaignConfigurationError(_configuration_warning(
            campaign_id, field, f"se esperaba un arreglo JSON y se recibió {type(payload).__name__}."))

    for config_index, config in enumerate(payload):
        config_path = f"push_data[{config_index}]"
        if not isinstance(config, dict):
            raise CampaignConfigurationError(_configuration_warning(
                campaign_id, config_path, "se esperaba un objeto JSON de configuración."))
        coupons = config.get("gpCoupons")
        if not isinstance(coupons, list):
            raise CampaignConfigurationError(_configuration_warning(
                campaign_id, f"{config_path}.gpCoupons",
                "se esperaba un arreglo JSON de cupones."))

        for coupon_index, coupon in enumerate(coupons):
            coupon_path = f"{config_path}.gpCoupons[{coupon_index}]"
            if not isinstance(coupon, dict):
                raise CampaignConfigurationError(_configuration_warning(
                    campaign_id, coupon_path, "se esperaba un objeto JSON de cupón."))
            if coupon.get("isWinner") is not True:
                continue

            store_config = coupon.get("storeConfig")
            stores = store_config.get("stores") if isinstance(store_config, dict) else None
            if not isinstance(stores, list):
                raise CampaignConfigurationError(_configuration_warning(
                    campaign_id, f"{coupon_path}.storeConfig.stores",
                    "se esperaba un arreglo JSON para los horarios por tienda."))

            for store_index, store in enumerate(stores):
                store_path = f"{coupon_path}.storeConfig.stores[{store_index}]"
                if not isinstance(store, dict):
                    raise CampaignConfigurationError(_configuration_warning(
                        campaign_id, store_path, "se esperaba un objeto JSON de tienda."))
                slots = store.get("slots")
                if not isinstance(slots, list):
                    raise CampaignConfigurationError(_configuration_warning(
                        campaign_id, f"{store_path}.slots",
                        "se esperaba un arreglo JSON de horarios."))
                for slot_index, slot in enumerate(slots):
                    slot_path = f"{store_path}.slots[{slot_index}]"
                    if not isinstance(slot, dict):
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, slot_path,
                            "se esperaba un objeto JSON de horario."))
                    try:
                        date.fromisoformat(slot["dateFrom"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, f"{slot_path}.dateFrom",
                            "fecha ausente o inválida; se esperaba YYYY-MM-DD.")) from exc
                    try:
                        time.fromisoformat(slot["timeFrom"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, f"{slot_path}.timeFrom",
                            "hora ausente o inválida; se esperaba HH:MM[:SS].")) from exc
                    quantity = slot.get("qty")
                    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, f"{slot_path}.qty",
                            "cantidad ausente o inválida; se esperaba un entero no negativo."))
    return payload


def _campaign_config_values(configs: dict[int, list]) -> tuple[str, dict]:
    values, params = [], {}
    for index, (campaign_id, payload) in enumerate(sorted(configs.items())):
        id_key, payload_key = f"campaign_id_{index}", f"payload_{index}"
        values.append(f"(%({id_key})s::int, %({payload_key})s::jsonb)")
        params[id_key] = campaign_id
        params[payload_key] = json.dumps(payload)
    return ",\n        ".join(values), params


def fetch_reporte_diario(db: DatabaseConfig) -> pd.DataFrame:
    """Ejecuta la consulta (solo lectura) y valida el resultado.
    Lanza excepción si la BD falla o el resultado está incompleto."""
    with get_connection(db) as conn:
        cur = conn.cursor()
        cur.execute(_CREATED_TYPE_SQL)
        row = cur.fetchone()
        if not row or row[0] not in _CREATED_DATE:
            raise RuntimeError('No se pudo determinar el tipo de "CustomerPrizeWins"."createdAt".')
        with conn.cursor() as campaign_cursor:
            campaign_cursor.execute(_CAMPAIGNS_SQL, ([cid for cid, _ in CAMPANIAS],))
            campaign_rows = campaign_cursor.fetchall()

        configs = {
            int(campaign_id): _decode_campaign_payload(int(campaign_id), payload)
            for campaign_id, payload in campaign_rows
        }
        missing_campaigns = sorted({cid for cid, _ in CAMPANIAS} - set(configs))
        if missing_campaigns:
            details = [
                _configuration_warning(cid, "push_data", "no se encontró la campaña.")
                for cid in missing_campaigns
            ]
            raise CampaignConfigurationError("; ".join(details))

        config_values, config_params = _campaign_config_values(configs)
        sql = REPORT_SQL.format(
            created_date=_CREATED_DATE[row[0]],
            created_le_corte=_CREATED_LE_CORTE[row[0]],
            campaign_configs=config_values)
        df = pd.read_sql_query(sql, conn, params=config_params)

    if list(df["pais"]) != list(PAISES):
        raise RuntimeError(f"Resultado incompleto: países recibidos {list(df['pais'])}.")
    if df[["fecha", "hora_corte"]].nunique().max() != 1:
        raise RuntimeError("Resultado inconsistente: fecha/hora de corte distintas entre países.")
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="raise")
        if df[col].isna().any():
            raise RuntimeError(f"Resultado incompleto: valores nulos en {col}.")
        df[col] = df[col].astype(int)
    if (df["premios_a_entregar_corte"] > df["premios_a_entregar_total"]).any():
        raise RuntimeError("Resultado inconsistente: habilitados al corte > a entregar hoy.")
    if (df["total_entregados_acumulados"] < df["premios_entregados_hoy"]).any():
        raise RuntimeError("Resultado inconsistente: acumulado entregado < entregados hoy.")
    logger.info("Reporte diario premios: %d filas.", len(df))
    return df
