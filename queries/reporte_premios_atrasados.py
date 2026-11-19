"""Consulta SELECT de configuración de premios para el reporte por país."""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import DatabaseConfig
from queries.reporte_diario_premios import (
    CAMPANIAS,
    PAISES,
    TZ,
    _unique_campaign_configs,
)
from utils.db import get_connection

logger = logging.getLogger(__name__)
CAMPAIGN_IDS = tuple(campaign_id for campaign_id, _ in CAMPANIAS)

# La configuración, hora de corte, títulos de premio y existencia de tiendas se
# leen en una sola sentencia. Los contadores se exponen en su nivel nativo:
# storeQty por cupón/tienda y won por cupón; ninguno se asigna a un slot.
REPORT_SQL = """
WITH corte AS (
    SELECT NOW() AS fecha_hora_corte
)
SELECT
    c.id AS campaign_id,
    c.name AS campaign_name,
    c.status AS campaign_status,
    ct.fecha_hora_corte,
    c.push_data,
    COALESCE((
        SELECT jsonb_object_agg(cupones.coupon_id::text, r.title)
        FROM (
            SELECT DISTINCT NULLIF(cupon->>'couponId', '')::int AS coupon_id
            FROM jsonb_array_elements(c.push_data::jsonb) AS configs(configuracion)
            CROSS JOIN LATERAL jsonb_array_elements(configuracion->'gpCoupons') AS coupons(cupon)
            WHERE cupon->>'isWinner' IN ('true', '1')
              AND NULLIF(cupon->>'couponId', '') IS NOT NULL
        ) cupones
        JOIN rewards r ON r.id = cupones.coupon_id
    ), '{}'::jsonb) AS premios,
    COALESCE((
        SELECT jsonb_object_agg(
            tiendas.store_id::text,
            jsonb_build_object(
                'commercial_name', s.commercial_name,
                'public_name', s.public_name
            )
        )
        FROM (
            SELECT DISTINCT NULLIF(tienda->>'id', '')::int AS store_id
            FROM jsonb_array_elements(c.push_data::jsonb) AS configs(configuracion)
            CROSS JOIN LATERAL jsonb_array_elements(configuracion->'gpCoupons') AS coupons(cupon)
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(cupon->'storeConfig'->'stores', '[]'::jsonb)
            ) AS configured_stores(tienda)
            WHERE cupon->>'isWinner' IN ('true', '1')
              AND NULLIF(tienda->>'id', '') IS NOT NULL
        ) tiendas
        JOIN stores s ON s.id = tiendas.store_id
    ), '{}'::jsonb) AS tiendas_validas
FROM campaigns c
CROSS JOIN corte ct
WHERE c.id = ANY(%s)
ORDER BY c.id;
"""

BALANCE_UNAVAILABLE = (
    "En la estructura de campaigns.push_data inspeccionada no se encontraron campos "
    "de entregados/disponibles por tienda y slot. storeQty aparece como stock "
    "configurado por cupón/tienda y coupon.won como contador agregado por cupón; "
    "ninguno se distribuye por fecha programada. slot.qty es cantidad configurada, "
    "no saldo. La fuente de exportación que proporciona saldos por registro no está "
    "en este repositorio."
)
MOVEMENTS_UNAVAILABLE = "No disponible: requiere comparación de cortes."
BALANCE_METRICS = (
    "entregados_programacion_hoy",
    "entregados_programaciones_hasta_hoy",
    "pendientes_anteriores",
    "pendientes_hoy",
    "pendientes_hasta_hoy",
    "cumplimiento_hoy",
    "cumplimiento_hasta_hoy",
)


def _json_object(value, label):
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON inválido en {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} debe ser un objeto JSON.")
    return value


def _positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        store_id = int(value)
    except (TypeError, ValueError):
        return None
    return store_id if store_id > 0 else None


def _quantity_field(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def _sql_integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdecimal():
        return int(value)
    return None


def _coupon_stock(coupon):
    """Reproduce la fórmula agregada de CONFIG_GP_SQL sin distribuirla."""
    is_global = coupon.get("storeConfig", {}).get("isGlobal") in (True, "true")
    if is_global:
        raw_stock = coupon.get("qty")
        stock_total = 0 if raw_stock is None else _sql_integer(raw_stock)
    else:
        store_qty = _json_object(coupon.get("storeQty"), "gpCoupons[].storeQty")
        values = []
        for value in store_qty.values():
            if value == "":
                continue
            parsed = _sql_integer(value)
            if parsed is None:
                raw_won = coupon.get("won")
                won = 0 if raw_won is None else _sql_integer(raw_won)
                return None, won, None
            values.append(parsed)
        stock_total = sum(values) if values else 0
    raw_won = coupon.get("won")
    won = 0 if raw_won is None else _sql_integer(raw_won)
    if stock_total is None or won is None:
        return stock_total, won, None
    difference = None if stock_total is None else stock_total - won
    return stock_total, won, difference


def _configuration_rows(campaign_rows, configs):
    schedules = []
    coupons = []
    store_balances = []
    audit = []
    for campaign_id, campaign_name, campaign_status, _, _, prizes_raw, stores_raw in campaign_rows:
        payload = configs[campaign_id]
        if payload is None:
            audit.append({
                "pais": dict(CAMPANIAS)[campaign_id],
                "campania": campaign_id,
                "campaign_name": campaign_name,
                "campaign_status": campaign_status,
                "configuracion": "No disponible",
                "fuente": "campaigns.push_data",
            })
            continue

        prizes = _json_object(prizes_raw, f"campaña {campaign_id}, premios")
        stores_valid = _json_object(stores_raw, f"campaña {campaign_id}, tiendas válidas")
        country = dict(CAMPANIAS)[campaign_id]
        found_winner = False
        for config_index, configuration in enumerate(payload):
            for coupon_index, coupon in enumerate(configuration["gpCoupons"]):
                if coupon.get("isWinner") is not True:
                    continue
                found_winner = True
                coupon_id = coupon.get("couponId")
                coupon_path = (
                    f"campaigns.push_data[{config_index}].gpCoupons[{coupon_index}]"
                )
                store_config = coupon["storeConfig"]
                stores = store_config["stores"]
                store_qty = _json_object(coupon.get("storeQty"), f"{coupon_path}.storeQty")
                stock_total, won, difference = _coupon_stock(coupon)
                coupons.append({
                    "pais": country,
                    "campania": campaign_id,
                    "campaign_name": campaign_name,
                    "premio": prizes.get(str(coupon_id)),
                    "coupon_id": coupon_id,
                    "campaign_status": campaign_status,
                    "is_winner": True,
                    "is_global": store_config.get("isGlobal"),
                    "qty_configurada_cupon": _quantity_field(coupon.get("qty")),
                    "stock_total_configuracion": stock_total,
                    "won_agregado_cupon": won,
                    "diferencia_agregada_cupon": difference,
                    "fuente": {
                        "premio": "rewards.title vía gpCoupons[].couponId",
                        "is_global": f"{coupon_path}.storeConfig.isGlobal",
                        "qty_configurada_cupon": f"{coupon_path}.qty",
                        "stock_total": (
                            f"{coupon_path}.qty si isGlobal=true; "
                            f"SUM({coupon_path}.storeQty) si isGlobal=false"
                        ),
                        "won": f"{coupon_path}.won (agregado por cupón)",
                        "diferencia": "stock_total - won según CONFIG_GP_SQL",
                    },
                })
                if not stores:
                    audit.append({
                        "pais": country,
                        "campania": campaign_id,
                        "campaign_name": campaign_name,
                        "premio": prizes.get(str(coupon_id)),
                        "coupon_id": coupon_id,
                        "campaign_status": campaign_status,
                        "is_winner": True,
                        "configuracion": "Premio ganador sin tiendas configuradas",
                        "fuente": f"{coupon_path}.storeConfig.stores",
                    })
                    continue

                for store_index, store in enumerate(stores):
                    store_id = _positive_id(store.get("id"))
                    store_path = f"{coupon_path}.storeConfig.stores[{store_index}]"
                    store_is_valid = (
                        store_id is not None
                        and str(store_id) in stores_valid
                        and bool(str(store.get("name", "")).strip())
                    )
                    configured_store_balance = _quantity_field(
                        store_qty.get(str(store_id)) if store_id is not None else None
                    )
                    store_balances.append({
                        "pais": country,
                        "campania": campaign_id,
                        "premio": prizes.get(str(coupon_id)),
                        "coupon_id": coupon_id,
                        "pdv_id": store_id,
                        "tienda_nombre": store.get("name"),
                        "pdv_valido": store_is_valid,
                        "storeQty_configurado": configured_store_balance,
                        "fuente": f"{coupon_path}.storeQty[{store_id}]",
                    })
                    slots = store["slots"]
                    if not slots:
                        audit.append({
                            "pais": country,
                            "campania": campaign_id,
                            "campaign_name": campaign_name,
                            "premio": prizes.get(str(coupon_id)),
                            "coupon_id": coupon_id,
                            "campaign_status": campaign_status,
                            "is_winner": True,
                            "pdv_id": store_id,
                            "tienda_nombre": store.get("name"),
                            "pdv_valido": store_is_valid,
                            "configuracion": "Tienda sin slots",
                            "fuente": f"{store_path}.slots",
                        })
                        continue

                    for slot_index, slot in enumerate(slots):
                        slot_path = f"{store_path}.slots[{slot_index}]"
                        record = {
                            "pais": country,
                            "campania": campaign_id,
                            "campaign_name": campaign_name,
                            "premio": prizes.get(str(coupon_id)),
                            "coupon_id": coupon_id,
                            "campaign_status": campaign_status,
                            "is_winner": True,
                            "pdv_id": store_id,
                            "tienda_nombre": store.get("name"),
                            "pdv_valido": store_is_valid,
                            "fecha_inicio": slot.get("dateFrom"),
                            "fecha_fin": slot.get("dateTo"),
                            "hora_inicio": slot.get("timeFrom"),
                            "hora_fin": slot.get("timeTo"),
                            "cantidad_slot_configurada": _quantity_field(slot.get("qty")),
                            "fuentes": {
                                "campania": "campaigns.id",
                                "premio": "rewards.title vía gpCoupons[].couponId",
                                "pdv_id": f"{store_path}.id; validado contra stores.id",
                                "tienda_nombre": f"{store_path}.name",
                                "fecha_inicio": f"{slot_path}.dateFrom (configuración vigente)",
                                "fecha_fin": f"{slot_path}.dateTo (vigencia)",
                                "hora_inicio": f"{slot_path}.timeFrom",
                                "hora_fin": f"{slot_path}.timeTo",
                                "cantidad_slot": f"{slot_path}.qty (no se interpreta como saldo)",
                                "identidad_programacion": (
                                    "Solo posición de slot en push_data; no hay ID estable"
                                ),
                            },
                        }
                        audit.append(record)
                        if store_is_valid:
                            schedules.append(record)
        if not found_winner:
            audit.append({
                "pais": country,
                "campania": campaign_id,
                "campaign_name": campaign_name,
                "campaign_status": campaign_status,
                "configuracion": "No hay gpCoupons ganadores",
                "fuente": "campaigns.push_data[].gpCoupons[].isWinner",
            })
    country_order = {country: index for index, country in enumerate(PAISES)}
    schedules.sort(key=lambda row: (
        country_order[row["pais"]],
        row["campania"],
        row["pdv_id"],
        row["fecha_inicio"] or "",
        row["fecha_fin"] or "",
        row["hora_inicio"] or "",
        row["hora_fin"] or "",
    ))
    coupons.sort(key=lambda row: (
        country_order[row["pais"]], row["campania"], str(row["coupon_id"])
    ))
    store_balances.sort(key=lambda row: (
        country_order[row["pais"]], row["campania"], row["pdv_id"] or 0
    ))
    return schedules, coupons, store_balances, audit


def _unavailable_campaign(campaign_id, country):
    return {
        "campaign_id": campaign_id,
        "pais": country,
        "previstos_acumulados": None,
        "previstos_hoy": None,
        "entregados_programacion_hoy": None,
        "entregados_programaciones_hasta_hoy": None,
        "pendientes_anteriores": None,
        "pendientes_hoy": None,
        "pendientes_hasta_hoy": None,
        "cumplimiento_hoy": None,
        "cumplimiento_hasta_hoy": None,
        "motivo": BALANCE_UNAVAILABLE,
    }


def _campaign_configuration_rows(campaign_rows, configs):
    rows = []
    for row in campaign_rows:
        campaign_id = row[0]
        payload = configs[campaign_id]
        rows.append({
            "pais": dict(CAMPANIAS)[campaign_id],
            "campania": campaign_id,
            "campaign_name": row[1],
            "configuracion_disponible": payload is not None,
            "tiene_ganadores": (
                any(
                    coupon.get("isWinner") is True
                    for configuration in payload
                    for coupon in configuration["gpCoupons"]
                )
                if payload is not None else None
            ),
        })
    return rows


def _calculate_schedule_metrics(campaigns, country_metrics, schedules, config_rows, cutoff):
    """Calcula únicamente programación configurada; saldos permanecen desconocidos."""
    report_date = cutoff.astimezone(ZoneInfo(TZ)).date()
    config_by_campaign = {row["campania"]: row for row in config_rows}
    for campaign in campaigns:
        config = config_by_campaign[campaign["campaign_id"]]
        if not config["configuracion_disponible"]:
            continue
        campaign_slots = [
            row for row in schedules
            if row["campania"] == campaign["campaign_id"]
        ]
        if not config["tiene_ganadores"]:
            campaign_slots = []
        quantities = [row["cantidad_slot_configurada"] for row in campaign_slots]
        if any(quantity is None for quantity in quantities):
            continue
        dated_slots = [
            (row, datetime.strptime(row["fecha_inicio"], "%Y-%m-%d").date())
            for row in campaign_slots
        ]
        campaign["previstos_acumulados"] = sum(
            row["cantidad_slot_configurada"]
            for row, start_date in dated_slots
            if start_date <= report_date
        )
        campaign["previstos_hoy"] = sum(
            row["cantidad_slot_configurada"]
            for row, start_date in dated_slots
            if start_date == report_date
        )

    for country, metrics in country_metrics.items():
        country_campaigns = [
            campaign for campaign in campaigns if campaign["pais"] == country
        ]
        for key in ("previstos_acumulados", "previstos_hoy"):
            metrics[key] = (
                None
                if any(campaign[key] is None for campaign in country_campaigns)
                else sum(campaign[key] for campaign in country_campaigns)
            )


def _validate_metrics(campaigns, countries):
    """Solo saldos sin fuente confiable deben permanecer no disponibles."""
    for campaign in campaigns:
        for key in BALANCE_METRICS:
            if campaign[key] is not None:
                raise RuntimeError(f"Métrica no sustentada debe permanecer no disponible: {key}.")
        if (campaign["previstos_acumulados"] is None) != (
            campaign["previstos_hoy"] is None
        ):
            raise RuntimeError(
                "La programación del día y el acumulado deben compartir disponibilidad."
            )
    for country in countries.values():
        for key in BALANCE_METRICS:
            if country[key] is not None:
                raise RuntimeError(
                    f"Métrica de saldo sin fuente confiable debe permanecer no disponible: {key}."
                )
        if country["pdv_atrasados"] is not None:
            raise RuntimeError("Sin saldos por slot/PDV no se pueden validar los atrasos.")
        if (country["previstos_acumulados"] is None) != (
            country["previstos_hoy"] is None
        ):
            raise RuntimeError(
                "Los totales de programación del país deben compartir disponibilidad."
            )


def fetch_reporte_premios_atrasados(db: DatabaseConfig):
    """Lee la configuración actual y una sola hora de corte, siempre con SELECT."""
    with get_connection(db) as conn:
        with conn.cursor() as cursor:
            cursor.execute(REPORT_SQL, (list(CAMPAIGN_IDS),))
            campaign_rows = cursor.fetchall()

    if len(campaign_rows) != len(CAMPAIGN_IDS):
        raise RuntimeError(
            f"Resultado incompleto: se esperaban {len(CAMPAIGN_IDS)} campañas, "
            f"se recibieron {len(campaign_rows)}."
        )
    if tuple(row[0] for row in campaign_rows) != CAMPAIGN_IDS:
        raise RuntimeError("Resultado incompleto: identificadores de campaña inesperados.")
    cutoffs = {row[3] for row in campaign_rows}
    if len(cutoffs) != 1:
        raise RuntimeError("Las campañas recibieron instantes de corte distintos.")
    cutoff = next(iter(cutoffs))
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
        raise RuntimeError("El corte único no es un timestamp con zona.")

    config_rows = [(row[0], row[4]) for row in campaign_rows]
    configs = _unique_campaign_configs(config_rows)
    for campaign_id in CAMPAIGN_IDS:
        if campaign_id not in configs:
            logger.warning("Campaña %s: no se encontró campaigns.push_data.", campaign_id)
            configs[campaign_id] = None

    schedules, coupon_stocks, store_balances, audit = _configuration_rows(
        campaign_rows, configs
    )
    campaign_sources = _campaign_configuration_rows(campaign_rows, configs)
    campaign_metrics = [
        _unavailable_campaign(campaign_id, country)
        for campaign_id, country in CAMPANIAS
    ]
    countries = {}
    for country in PAISES:
        selected = [campaign for campaign in campaign_metrics if campaign["pais"] == country]
        countries[country] = {
            key: None
            for key in (
                "previstos_acumulados",
                "previstos_hoy",
                "entregados_programacion_hoy",
                "entregados_programaciones_hasta_hoy",
                "pendientes_anteriores",
                "pendientes_hoy",
                "pendientes_hasta_hoy",
                "cumplimiento_hoy",
                "cumplimiento_hasta_hoy",
            )
        }
        countries[country].update({
            "detalle_campanias": selected,
            "pdv_atrasados": None,
            "motivo": BALANCE_UNAVAILABLE,
        })

    _calculate_schedule_metrics(
        campaign_metrics, countries, schedules, campaign_sources, cutoff
    )
    _validate_metrics(campaign_metrics, countries)
    return {
        "fecha_hora_corte": cutoff,
        "fecha": cutoff.astimezone(ZoneInfo(TZ)).date(),
        "paises": countries,
        "campanias": campaign_metrics,
        "programaciones": schedules,
        "cupones": coupon_stocks,
        "stocks_tienda": store_balances,
        "campanias_configuracion": campaign_sources,
        "auditoria": audit,
        "saldos_explicacion": BALANCE_UNAVAILABLE,
        "movimientos_explicacion": MOVEMENTS_UNAVAILABLE,
    }
