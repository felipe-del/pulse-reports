"""Compara dos lecturas del acumulado de configuración para campañas 825 y 826.

Solo ejecuta SELECT. No consulta estados de Telegram ni muestra datos personales.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from config.settings import config
from queries.reporte_diario_premios import (
    _CREATED_LE_CORTE,
    _CREATED_TYPE_SQL,
    _decode_campaign_payload,
    TZ,
)
from utils.db import get_connection


CAMPAIGN_IDS = (825, 826)
CUTOFF = datetime.fromisoformat("2026-11-12T10:26:00-06:00")

CAMPAIGNS_SQL = """
SELECT id, push_data
FROM campaigns
WHERE id = ANY(%s)
ORDER BY id;
"""

ADJUDICATIONS_SQL = """
SELECT
    cpw."campaignId",
    COUNT(*) AS registros,
    COALESCE(SUM(cpw."totalPrizes"), 0) AS adjudicados_acumulados
FROM "CustomerPrizeWins" cpw
CROSS JOIN (SELECT %(fecha_hora_corte)s::timestamptz AS fecha_hora_corte) ct
WHERE cpw."campaignId" IN (825, 826)
  AND {created_le_corte}
GROUP BY cpw."campaignId"
ORDER BY cpw."campaignId";
"""


def _included_by_date(target_date, slot_time, cutoff_date, cutoff_time):
    return target_date < cutoff_date or (
        target_date == cutoff_date and slot_time <= cutoff_time
    )


def summarize_config_rows(campaign_rows, cutoff=CUTOFF):
    """Compara dateTo/timeFrom con dateFrom hasta el día completo, sin deduplicar."""
    local_cut = cutoff.astimezone(ZoneInfo(TZ))
    result = {
        campaign_id: {
            "configuration_rows": 0,
            "winning_slots": 0,
            "date_to_slots": 0,
            "date_to_qty": 0,
            "date_from_slots": 0,
            "date_from_qty": 0,
            "date_from_only_slots": 0,
            "date_from_only_qty": 0,
            "date_to_only_slots": 0,
            "date_to_only_qty": 0,
        }
        for campaign_id in CAMPAIGN_IDS
    }

    for campaign_id, raw_payload in campaign_rows:
        campaign_id = int(campaign_id)
        if campaign_id not in result:
            continue
        campaign = result[campaign_id]
        campaign["configuration_rows"] += 1
        payload = _decode_campaign_payload(campaign_id, raw_payload)
        for configuration in payload:
            for coupon in configuration["gpCoupons"]:
                if coupon.get("isWinner") is not True:
                    continue
                for store in coupon["storeConfig"]["stores"]:
                    for slot in store["slots"]:
                        campaign["winning_slots"] += 1
                        date_from = datetime.fromisoformat(slot["dateFrom"]).date()
                        date_to = datetime.fromisoformat(slot["dateTo"]).date()
                        time_from = time.fromisoformat(slot["timeFrom"])
                        quantity = slot["qty"]
                        included_by_to = _included_by_date(
                            date_to, time_from, local_cut.date(),
                            local_cut.time().replace(tzinfo=None))
                        included_by_from = date_from <= local_cut.date()
                        if included_by_to:
                            campaign["date_to_slots"] += 1
                            campaign["date_to_qty"] += quantity
                        if included_by_from:
                            campaign["date_from_slots"] += 1
                            campaign["date_from_qty"] += quantity
                        if included_by_from and not included_by_to:
                            campaign["date_from_only_slots"] += 1
                            campaign["date_from_only_qty"] += quantity
                        elif included_by_to and not included_by_from:
                            campaign["date_to_only_slots"] += 1
                            campaign["date_to_only_qty"] += quantity
    return result


def _fetch_diagnostic():
    with get_connection(config.db_hn) as conn:
        with conn.cursor() as cursor:
            cursor.execute(_CREATED_TYPE_SQL)
            row = cursor.fetchone()
        if not row or row[0] not in _CREATED_LE_CORTE:
            raise RuntimeError('No se pudo determinar el tipo de "CustomerPrizeWins"."createdAt".')
        created_type = row[0]

        with conn.cursor() as cursor:
            cursor.execute(CAMPAIGNS_SQL, (list(CAMPAIGN_IDS),))
            campaign_rows = cursor.fetchall()
        with conn.cursor() as cursor:
            cursor.execute(
                ADJUDICATIONS_SQL.format(created_le_corte=_CREATED_LE_CORTE[created_type]),
                {"fecha_hora_corte": CUTOFF},
            )
            adjudication_rows = cursor.fetchall()

    totals = summarize_config_rows(campaign_rows)
    if any(totals[campaign_id]["configuration_rows"] == 0 for campaign_id in CAMPAIGN_IDS):
        raise RuntimeError("No se encontró configuración para todas las campañas solicitadas.")
    adjudications = {
        int(campaign_id): {"records": int(records), "quantity": int(quantity)}
        for campaign_id, records, quantity in adjudication_rows
    }
    return created_type, totals, adjudications


def main():
    created_type, totals, adjudications = _fetch_diagnostic()
    local_cut = CUTOFF.astimezone(ZoneInfo(TZ))
    print(f"Corte local: {local_cut:%Y-%m-%d %H:%M:%S %Z}")
    print(f'Tipo de CustomerPrizeWins.createdAt: {created_type}')
    print("Configuración vigente al consultar; no reconstruye la configuración del corte.")
    print("dateTo usa el criterio previo dateTo/timeFrom; dateFrom incluye todo el día.")
    print("timeFrom no se aplica al acumulado dateFrom; su significado funcional no está confirmado.")
    for campaign_id in CAMPAIGN_IDS:
        values = totals[campaign_id]
        awards = adjudications.get(campaign_id, {"records": 0, "quantity": 0})
        print(f"\nCampaña {campaign_id} (filas de configuración: {values['configuration_rows']})")
        print(f"  Slots ganadores: {values['winning_slots']}")
        print(
            "  Acumulado por dateTo/timeFrom: "
            f"{values['date_to_qty']} ({values['date_to_slots']} slots)"
        )
        print(
            "  Acumulado por dateFrom (día completo): "
            f"{values['date_from_qty']} ({values['date_from_slots']} slots)"
        )
        print(
            "  Cambian: incluidos solo por dateFrom: "
            f"{values['date_from_only_qty']} ({values['date_from_only_slots']} slots); "
            "incluidos solo por dateTo: "
            f"{values['date_to_only_qty']} ({values['date_to_only_slots']} slots)"
        )
        print(
            "  Adjudicados acumulados: "
            f"{awards['quantity']} en {awards['records']} registros"
        )


if __name__ == "__main__":
    main()
