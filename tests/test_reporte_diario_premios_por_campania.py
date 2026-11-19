import unittest
from datetime import date, datetime, timezone

from queries.reporte_diario_premios import _schedule_totals
from queries.reporte_diario_premios_campania import aggregate_campaign_rows
from reports.reporte_diario_premios_campania_mensaje import build_mensaje_por_campania


COUNTRY_CAMPAIGNS = {
    "Guatemala": (818, 819, 820),
    "El Salvador": (825, 826),
    "Honduras": (828, 829),
}


def _campaign_row(campaign_id, country, **overrides):
    row = {
        "campaign_id": campaign_id,
        "pais": country,
        "programado_hoy_config_vigente": 0,
        "programados_nuevos_hoy": 0,
        "programados_arrastrados_hoy": 0,
        "arrastrados_por_fecha_inicio": {},
        "programados_vigentes_fin_posterior_hoy": 0,
        "programado_acumulado_config_vigente": 0,
        "adjudicados_hoy_al_corte": 0,
        "adjudicados_acumulados_al_corte": 0,
    }
    row.update(overrides)
    return row


def _payload(slots):
    return [{
        "gpCoupons": [{
            "isWinner": True,
            "storeConfig": {"stores": [{"slots": slots}]},
        }]
    }]


def _sample_report():
    rows = [
        _campaign_row(818, "Guatemala", programado_hoy_config_vigente=8,
                      programados_nuevos_hoy=5, programados_arrastrados_hoy=3,
                      arrastrados_por_fecha_inicio={date(2026, 11, 11): 3},
                      programado_acumulado_config_vigente=20,
                      adjudicados_hoy_al_corte=2, adjudicados_acumulados_al_corte=15),
        _campaign_row(819, "Guatemala", programado_hoy_config_vigente=2,
                      programados_nuevos_hoy=2, programado_acumulado_config_vigente=12,
                      adjudicados_hoy_al_corte=1, adjudicados_acumulados_al_corte=10),
        _campaign_row(820, "Guatemala", programado_acumulado_config_vigente=5,
                      adjudicados_acumulados_al_corte=3),
        _campaign_row(825, "El Salvador", programado_hoy_config_vigente=4,
                      programados_nuevos_hoy=4, programados_vigentes_fin_posterior_hoy=6,
                      programado_acumulado_config_vigente=25,
                      adjudicados_hoy_al_corte=3, adjudicados_acumulados_al_corte=21),
        _campaign_row(826, "El Salvador", programado_hoy_config_vigente=2,
                      programados_arrastrados_hoy=2,
                      arrastrados_por_fecha_inicio={date(2026, 11, 10): 2},
                      programado_acumulado_config_vigente=16,
                      adjudicados_hoy_al_corte=2, adjudicados_acumulados_al_corte=18),
        _campaign_row(828, "Honduras", programado_hoy_config_vigente=7,
                      programados_nuevos_hoy=7, programado_acumulado_config_vigente=30,
                      adjudicados_hoy_al_corte=4, adjudicados_acumulados_al_corte=25),
        _campaign_row(829, "Honduras", programado_hoy_config_vigente=1,
                      programados_arrastrados_hoy=1,
                      arrastrados_por_fecha_inicio={date(2026, 11, 11): 1},
                      programado_acumulado_config_vigente=11,
                      adjudicados_hoy_al_corte=1, adjudicados_acumulados_al_corte=12),
    ]
    return {
        "fecha_hora_corte": datetime(2026, 11, 12, 16, 26, tzinfo=timezone.utc),
        "campanias": rows,
        "paises": aggregate_campaign_rows(rows),
    }


class CampaignReportTests(unittest.TestCase):
    def test_schedule_metrics_count_units_and_start_date_once(self):
        config = _payload([
            {
                "dateFrom": "2026-11-12", "dateTo": "2026-11-12",
                "timeFrom": "14:00", "qty": 5,
            },
            {
                "dateFrom": "2026-11-10", "dateTo": "2026-11-20",
                "timeFrom": "08:00", "qty": 3,
            },
            {
                "dateFrom": "2026-11-11", "dateTo": "2026-11-12",
                "timeFrom": "09:00", "qty": 2,
            },
        ])
        totals = _schedule_totals({825: config}, date(2026, 11, 12))[825]
        self.assertEqual(totals["programado_hoy_config_vigente"], 7)
        self.assertEqual(totals["programados_nuevos_hoy"], 5)
        self.assertEqual(totals["programados_arrastrados_hoy"], 2)
        self.assertEqual(totals["programados_vigentes_fin_posterior_hoy"], 3)
        self.assertEqual(totals["programado_acumulado_config_vigente"], 10)

    def test_country_totals_equal_campaign_sums(self):
        rows = [
            _campaign_row(818, "Guatemala", programado_hoy_config_vigente=3,
                          programados_nuevos_hoy=3, programado_acumulado_config_vigente=8,
                          adjudicados_hoy_al_corte=1, adjudicados_acumulados_al_corte=4),
            _campaign_row(819, "Guatemala", programado_hoy_config_vigente=2,
                          programados_arrastrados_hoy=2,
                          arrastrados_por_fecha_inicio={date(2026, 11, 10): 2},
                          programado_acumulado_config_vigente=5,
                          adjudicados_hoy_al_corte=2, adjudicados_acumulados_al_corte=6),
            _campaign_row(820, "Guatemala", programado_acumulado_config_vigente=4,
                          adjudicados_acumulados_al_corte=3),
            _campaign_row(825, "El Salvador", programado_hoy_config_vigente=7,
                          programados_nuevos_hoy=7, programado_acumulado_config_vigente=9,
                          adjudicados_hoy_al_corte=2, adjudicados_acumulados_al_corte=10),
            _campaign_row(826, "El Salvador", programado_acumulado_config_vigente=6,
                          adjudicados_hoy_al_corte=1, adjudicados_acumulados_al_corte=5),
            _campaign_row(828, "Honduras", programado_hoy_config_vigente=4,
                          programados_nuevos_hoy=4, programado_acumulado_config_vigente=8,
                          adjudicados_hoy_al_corte=3, adjudicados_acumulados_al_corte=9),
            _campaign_row(829, "Honduras", programado_acumulado_config_vigente=2,
                          adjudicados_hoy_al_corte=1, adjudicados_acumulados_al_corte=4),
        ]
        countries = aggregate_campaign_rows(rows)
        by_country = {row["pais"]: row for row in countries}
        self.assertEqual(by_country["Guatemala"]["programado_acumulado_config_vigente"], 17)
        self.assertEqual(by_country["Guatemala"]["adjudicados_hoy_al_corte"], 3)
        self.assertEqual(by_country["Guatemala"]["adjudicados_acumulados_al_corte"], 13)
        for country, campaign_ids in COUNTRY_CAMPAIGNS.items():
            subset = [row for row in rows if row["campaign_id"] in campaign_ids]
            for field in (
                "programado_hoy_config_vigente",
                "programados_nuevos_hoy",
                "programados_arrastrados_hoy",
                "programados_vigentes_fin_posterior_hoy",
                "programado_acumulado_config_vigente",
                "adjudicados_hoy_al_corte",
                "adjudicados_acumulados_al_corte",
            ):
                self.assertEqual(
                    by_country[country][field],
                    sum(row[field] for row in subset),
                    (country, field),
                )

    def test_missing_campaign_metric_propagates_instead_of_becoming_zero(self):
        rows = [
            _campaign_row(825, "El Salvador", programado_acumulado_config_vigente=10),
            _campaign_row(826, "El Salvador", programado_acumulado_config_vigente=None),
        ]
        rows += [
            _campaign_row(cid, country)
            for country, campaign_ids in COUNTRY_CAMPAIGNS.items()
            if country != "El Salvador"
            for cid in campaign_ids
        ]
        countries = aggregate_campaign_rows(rows)
        el_salvador = next(row for row in countries if row["pais"] == "El Salvador")
        self.assertIsNone(el_salvador["programado_acumulado_config_vigente"])

    def test_campaign_message_contains_campaign_blocks_and_same_cut(self):
        message = build_mensaje_por_campania(_sample_report())
        for campaign_id in (818, 819, 820, 825, 826, 828, 829):
            self.assertIn(f"📌 Campaña {campaign_id}", message)
        self.assertIn("📅 Fecha: 12/11/2026", message)
        self.assertIn("🕐 Hora de corte: 10:26 a. m.", message)
        self.assertIn("Adjudicaciones: CustomerPrizeWins; no equivalen a entrega física.", message)


if __name__ == "__main__":
    unittest.main()
