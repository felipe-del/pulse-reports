"""
queries/reporte_diario_premios.py
Consulta (solo SELECT) del reporte diario Gane Premios por país.
"""
import json
import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

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

_CUT_SQL = "SELECT NOW();"

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
corte AS (
    SELECT
        %(fecha_hora_corte)s::timestamptz AS fecha_hora_corte,
        (%(fecha_hora_corte)s::timestamptz AT TIME ZONE 'America/Guatemala')::date
            AS fecha_hoy,
        (%(fecha_hora_corte)s::timestamptz AT TIME ZONE 'America/Guatemala')::time
            AS hora_corte
),
ganadores AS (
    SELECT
        cpw."campaignId" AS campaign_id,
        COALESCE(SUM(cpw."totalPrizes"), 0) AS premios_adjudicados_hoy
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
        COALESCE(SUM(cpw."totalPrizes"), 0) AS total_adjudicados_acumulados
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

    SUM(COALESCE(g.premios_adjudicados_hoy, 0))
        AS premios_adjudicados_hoy,

    SUM(COALESCE(ga.total_adjudicados_acumulados, 0))
        AS premios_adjudicados_acumulados

FROM campanias ca
CROSS JOIN corte ct
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

NUMERIC_COLS = ("premios_adjudicados_hoy", "premios_adjudicados_acumulados")

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
                        date_from = slot["dateFrom"]
                        if (
                            not isinstance(date_from, str)
                            or date.fromisoformat(date_from).isoformat() != date_from
                        ):
                            raise ValueError("fecha fuera del formato YYYY-MM-DD")
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, f"{slot_path}.dateFrom",
                            "fecha ausente o inválida; se esperaba YYYY-MM-DD.")) from exc
                    try:
                        date_to = slot["dateTo"]
                        if (
                            not isinstance(date_to, str)
                            or date.fromisoformat(date_to).isoformat() != date_to
                        ):
                            raise ValueError("fecha fuera del formato YYYY-MM-DD")
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, f"{slot_path}.dateTo",
                            "fecha ausente o inválida; se esperaba YYYY-MM-DD.")) from exc
                    if date.fromisoformat(date_from) > date.fromisoformat(date_to):
                        raise CampaignConfigurationError(_configuration_warning(
                            campaign_id, slot_path,
                            "dateFrom no puede ser posterior a dateTo."))
                    try:
                        time_from = slot["timeFrom"]
                        if not isinstance(time_from, str):
                            raise ValueError("hora no textual")
                        parsed_time = time.fromisoformat(time_from)
                        if parsed_time.tzinfo is not None:
                            raise ValueError("hora con zona horaria no admitida")
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


def _unique_campaign_configs(campaign_rows) -> dict[int, list | None]:
    configs = {}
    for campaign_id, raw_payload in campaign_rows:
        campaign_id = int(campaign_id)
        if campaign_id in configs and configs[campaign_id] is None:
            continue
        try:
            payload = _decode_campaign_payload(campaign_id, raw_payload)
        except CampaignConfigurationError:
            configs[campaign_id] = None
            continue
        if campaign_id in configs:
            if configs[campaign_id] != payload:
                _configuration_warning(
                    campaign_id, "push_data",
                    "se recibieron configuraciones duplicadas y distintas; sus cantidades quedan no disponibles.")
                configs[campaign_id] = None
            continue
        configs[campaign_id] = payload
    return configs


def _schedule_totals(configs: dict[int, list | None],
                     fecha: date) -> dict[int, dict[str, int | None]]:
    """Clasifica qty configurado por inicio y fin vigentes, sin inferir saldo."""
    totals = {}
    for campaign_id, payload in configs.items():
        totals[campaign_id] = {
            "programado_hoy_config_vigente": None if payload is None else 0,
            "programados_nuevos_hoy": None if payload is None else 0,
            "programados_arrastrados_hoy": None if payload is None else 0,
            "arrastrados_por_fecha_inicio": None if payload is None else {},
            "programados_vigentes_fin_posterior_hoy": None if payload is None else 0,
            "programado_acumulado_config_vigente": None if payload is None else 0,
        }
    for campaign_id, payload in configs.items():
        if payload is None:
            continue
        for config in payload:
            for coupon in config["gpCoupons"]:
                if coupon.get("isWinner") is not True:
                    continue
                stores = coupon["storeConfig"]["stores"]
                for store in stores:
                    for slot in store["slots"]:
                        fecha_inicio = date.fromisoformat(slot["dateFrom"])
                        fecha_programada = date.fromisoformat(slot["dateTo"])
                        quantity = slot["qty"]
                        if fecha_inicio <= fecha <= fecha_programada:
                            if fecha_programada == fecha and fecha_inicio == fecha:
                                totals[campaign_id]["programados_nuevos_hoy"] += quantity
                                totals[campaign_id]["programado_hoy_config_vigente"] += quantity
                            elif fecha_programada == fecha and fecha_inicio < fecha:
                                totals[campaign_id]["programados_arrastrados_hoy"] += quantity
                                totals[campaign_id]["programado_hoy_config_vigente"] += quantity
                                by_start = totals[campaign_id]["arrastrados_por_fecha_inicio"]
                                by_start[fecha_inicio] = by_start.get(fecha_inicio, 0) + quantity
                            elif fecha_programada > fecha:
                                totals[campaign_id][
                                    "programados_vigentes_fin_posterior_hoy"
                                ] += quantity
                        if fecha_inicio <= fecha:
                            totals[campaign_id]["programado_acumulado_config_vigente"] += quantity
    return totals


def _country_schedule_total(schedule: dict, campaign_ids: list[int], metric: str):
    values = [schedule[cid][metric] for cid in campaign_ids]
    return None if any(value is None for value in values) else sum(values)


def _country_rollover_breakdown(schedule: dict, campaign_ids: list[int]):
    if any(schedule[cid]["arrastrados_por_fecha_inicio"] is None for cid in campaign_ids):
        return None
    result = {}
    for campaign_id in campaign_ids:
        for start_date, quantity in schedule[campaign_id]["arrastrados_por_fecha_inicio"].items():
            result[start_date] = result.get(start_date, 0) + quantity
    return result


def _validate_adjudication_totals(df: pd.DataFrame) -> None:
    if (df["premios_adjudicados_acumulados"] < df["premios_adjudicados_hoy"]).any():
        raise RuntimeError("Resultado inconsistente: adjudicados acumulados < adjudicados hoy.")


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

        with conn.cursor() as cut_cursor:
            cut_cursor.execute(_CUT_SQL)
            cut_row = cut_cursor.fetchone()
        if (
            not cut_row
            or not isinstance(cut_row[0], datetime)
            or cut_row[0].tzinfo is None
        ):
            raise RuntimeError("No se pudo obtener un corte único de fecha y hora.")
        fecha_hora_corte = cut_row[0]
        local_cut = fecha_hora_corte.astimezone(ZoneInfo(TZ))

        configs = _unique_campaign_configs(campaign_rows)
        missing_campaigns = sorted({cid for cid, _ in CAMPANIAS} - set(configs))
        for campaign_id in missing_campaigns:
            _configuration_warning(campaign_id, "push_data", "no se encontró la campaña.")
            configs[campaign_id] = None

        sql = REPORT_SQL.format(
            created_date=_CREATED_DATE[row[0]],
            created_le_corte=_CREATED_LE_CORTE[row[0]])
        df = pd.read_sql_query(
            sql, conn, params={"fecha_hora_corte": fecha_hora_corte})

    if list(df["pais"]) != list(PAISES):
        raise RuntimeError(f"Resultado incompleto: países recibidos {list(df['pais'])}.")
    if df[["fecha", "hora_corte"]].nunique().max() != 1:
        raise RuntimeError("Resultado inconsistente: fecha/hora de corte distintas entre países.")
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="raise")
        if df[col].isna().any():
            raise RuntimeError(f"Resultado incompleto: valores nulos en {col}.")
        df[col] = df[col].astype(int)
    _validate_adjudication_totals(df)

    schedule = _schedule_totals(configs, local_cut.date())
    for country in PAISES:
        campaign_ids = [cid for cid, pais in CAMPANIAS if pais == country]
        for metric in (
            "programado_hoy_config_vigente",
            "programados_nuevos_hoy",
            "programados_arrastrados_hoy",
            "programados_vigentes_fin_posterior_hoy",
            "programado_acumulado_config_vigente",
        ):
            df.loc[df["pais"] == country, metric] = _country_schedule_total(
                schedule, campaign_ids, metric)
        df.loc[df["pais"] == country, "arrastrados_por_fecha_inicio"] = [
            _country_rollover_breakdown(schedule, campaign_ids)
        ]
    logger.info(
        "Programación por vigencia: para hoy clasifica isWinner qty con dateFrom=dateTo=hoy "
        "como nueva, dateFrom<hoy=dateTo como arrastrada y dateFrom<=hoy<dateTo como vigente "
        "con fin posterior; el acumulado suma qty una vez por slot con dateFrom<=hoy e incluye "
        "todo el día sin filtrar timeFrom. Es configuración vigente, no historial ni saldo.")
    logger.info(
        "Pendientes/arrastres no calculados: no hay vínculo ni contador por estación/programación "
        "entre push_data y CustomerPrizeWins.")
    logger.info("Reporte diario premios: %d filas.", len(df))
    return df
