import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from queries import reporte_premios_atrasados as query
from reports.reporte_premios_atrasados_mensaje import (
    build_detalle_local,
    build_mensaje_premios_atrasados,
)
import reporte_premios_atrasados as command


CAMPAIGNS = (
    (818, "Guatemala"), (819, "Guatemala"), (820, "Guatemala"),
    (825, "El Salvador"), (826, "El Salvador"),
    (828, "Honduras"), (829, "Honduras"),
)
CUTOFF = datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc)


def _slot(start="2026-11-17", end="2026-11-18", time_from="23:00", qty=8):
    return {
        "dateFrom": start,
        "dateTo": end,
        "timeFrom": time_from,
        "timeTo": "23:59:00",
        "qty": qty,
        "probability": 99,
    }


def _payload(slot=None, store_id=77, store_name="PDV prueba"):
    winning = {
        "couponId": 501,
        "isWinner": True,
        "qty": 30,
        "won": 11,
        "storeQty": {str(store_id): 30},
        "storeConfig": {
            "isGlobal": False,
            "stores": [{
                "id": store_id,
                "name": store_name,
                "slots": [slot or _slot()],
            }],
        },
    }
    loser = {"isWinner": False}
    return [{
        "gpCoupons": [winning, loser],
    }]


def _campaign_rows(config_overrides=None):
    config_overrides = config_overrides or {}
    rows = []
    for campaign_id, country in CAMPAIGNS:
        payload = config_overrides.get(campaign_id, _payload())
        rows.append((
            campaign_id,
            f"Campaña {campaign_id}",
            1,
            CUTOFF,
            json.dumps(payload) if payload is not None else None,
            {"501": "Premio prueba"},
            {"77": {"commercial_name": "PDV prueba"}},
        ))
    return rows


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.cursor_instance = _Cursor(rows)

    def cursor(self):
        return self.cursor_instance


class ReportQueryTests(unittest.TestCase):
    def fetch(self, rows=None):
        conn = _Connection(rows or _campaign_rows())
        with mock.patch.object(
            query, "get_connection", return_value=contextlib.nullcontext(conn)
        ):
            report = query.fetch_reporte_premios_atrasados(mock.sentinel.db)
        return report, conn.cursor_instance

    def test_query_is_config_only_and_uses_one_cut(self):
        report, cursor = self.fetch()
        self.assertIn("FROM campaigns c", cursor.sql)
        self.assertIn("NOW() AS fecha_hora_corte", cursor.sql)
        self.assertIn("rewards r ON r.id = cupones.coupon_id", cursor.sql)
        self.assertIn("stores s ON s.id = tiendas.store_id", cursor.sql)
        self.assertNotIn("CustomerPrizeWins", cursor.sql)
        self.assertEqual(cursor.params, (list(query.CAMPAIGN_IDS),))
        self.assertEqual(report["fecha"], CUTOFF.astimezone(
            __import__("zoneinfo").ZoneInfo("America/Guatemala")).date())

    def test_unavailable_counters_never_become_zero_or_qty_based_metrics(self):
        report, _ = self.fetch()
        balance_metrics = (
            "entregados_programacion_hoy",
            "entregados_programaciones_hasta_hoy",
            "pendientes_anteriores",
            "pendientes_hoy",
            "pendientes_hasta_hoy",
            "cumplimiento_hoy",
            "cumplimiento_hasta_hoy",
        )
        for campaign in report["campanias"]:
            self.assertEqual(campaign["previstos_acumulados"], 8)
            self.assertEqual(campaign["previstos_hoy"], 8)
            for key in balance_metrics:
                self.assertIsNone(campaign[key], (campaign["campaign_id"], key))
        for country in report["paises"].values():
            self.assertEqual(country["previstos_acumulados"], 16 if len(
                country["detalle_campanias"]) == 2 else 24)
            self.assertEqual(country["previstos_hoy"], country["previstos_acumulados"])
            self.assertIsNone(country["pendientes_anteriores"])
            self.assertIsNone(country["pdv_atrasados"])
        audit = report["auditoria"][0]
        self.assertEqual(audit["cantidad_slot_configurada"], 8)
        self.assertNotIn("stock_storeQty_configurado", audit)
        self.assertEqual(len(report["cupones"]), 7)
        self.assertEqual(report["cupones"][0]["stock_total_configuracion"], 30)
        self.assertEqual(report["cupones"][0]["won_agregado_cupon"], 11)
        self.assertEqual(report["cupones"][0]["diferencia_agregada_cupon"], 19)
        self.assertEqual(report["stocks_tienda"][0]["storeQty_configurado"], 30)
        self.assertEqual(len(report["programaciones"]), 7)
        self.assertTrue(all(row["cantidad_slot_configurada"] == 8
                            for row in report["programaciones"]))

    def test_campaign_and_country_metrics_propagate_unknown_data(self):
        report, _ = self.fetch()
        self.assertEqual(len(report["campanias"]), 7)
        self.assertEqual(tuple(report["paises"]), query.PAISES)
        for country in query.PAISES:
            campaigns = report["paises"][country]["detalle_campanias"]
            self.assertTrue(campaigns)
            self.assertTrue(all(campaign["previstos_acumulados"] == 8
                                for campaign in campaigns))
            self.assertEqual(
                report["paises"][country]["previstos_acumulados"],
                16 if len(campaigns) == 2 else 24,
            )

    def test_missing_campaign_configuration_is_not_a_zero_balance(self):
        report, _ = self.fetch(_campaign_rows({818: None}))
        guatemala = report["paises"]["Guatemala"]
        self.assertIsNone(guatemala["previstos_acumulados"])
        self.assertIsNone(guatemala["previstos_hoy"])
        campaigns = {
            row["campaign_id"]: row for row in report["campanias"]
        }
        self.assertIsNone(campaigns[818]["previstos_acumulados"])
        self.assertEqual(campaigns[819]["previstos_acumulados"], 8)
        missing = next(row for row in report["auditoria"]
                       if row.get("campania") == 818)
        self.assertEqual(missing["configuracion"], "No disponible")
        text = build_mensaje_premios_atrasados(report)
        self.assertIn(
            "• 818: previstos hasta la fecha (qty configurada) N/D · "
            "entregados N/D · pendientes de",
            text,
        )

    def test_nonwinner_is_excluded_and_store_validity_is_checked(self):
        rows = _campaign_rows()
        bad_payload = _payload(store_id=998877, store_name="No registrada")
        rows[0] = (*rows[0][:4], json.dumps(bad_payload), rows[0][5], {})
        report, _ = self.fetch(rows)
        self.assertEqual(len(report["auditoria"]), 7)
        winner = report["auditoria"][0]
        self.assertTrue(winner["is_winner"])
        self.assertFalse(winner["pdv_valido"])
        self.assertFalse(any(row["campania"] == 818
                             for row in report["programaciones"]))

    def test_audit_shows_exact_fields_scope_and_no_historical_delivery_claim(self):
        report, _ = self.fetch()
        text = build_detalle_local(report)
        for fragment in (
            "Premio: Premio prueba",
            "PDV ID: 77",
            "Nombre: PDV prueba",
            "fecha_inicio vigente: 2026-11-17",
            "fecha_fin: 2026-11-18",
            "hora_inicio: 23:00",
            "hora_fin: 23:59:00",
            "qty slot configurada (no saldo): 8",
            "entregados/disponibles/días de atraso: N/D",
            "DIFERENCIAS AGREGADAS POR CUPÓN (una fila por cupón)",
            "stock_total: 30 | won agregado: 11",
            "diferencia agregada (stock_total - won): 19",
            "storeQty POR CUPÓN/TIENDA (no por fecha)",
        ):
            self.assertIn(fragment, text)

    def test_country_dashboard_has_all_campaigns_and_no_slot_dump(self):
        report, _ = self.fetch()
        text = build_mensaje_premios_atrasados(report)
        self.assertIn("📅 Fecha del reporte: 17/11/2026", text)
        self.assertIn("🕐 Corte de datos: 17/11/2026 10:00", text)
        country_campaigns = (
            ("🇭🇳 HONDURAS", (828, 829), 16),
            ("🇸🇻 EL SALVADOR", (825, 826), 16),
            ("🇬🇹 GUATEMALA", (818, 819, 820), 24),
        )
        for country, campaign_ids, total in country_campaigns:
            self.assertIn(country, text)
            self.assertIn(
                f"📈 Premios programados hasta la fecha (qty configurada): {total}",
                text,
            )
            for campaign_id in campaign_ids:
                self.assertIn(f"• {campaign_id}: previstos hasta la fecha", text)
                self.assertIn(f"• {campaign_id}: previstos hasta la fecha "
                              "(qty configurada) 8 · entregados N/D ·", text)
        self.assertIn("Atrasados pendientes de días anteriores: N/D", text)
        self.assertIn("Premios programados hasta la fecha (qty configurada): 24", text)
        self.assertIn("No disponible: falta saldo por PDV/programación.", text)
        self.assertIn("Limitación: la configuración no aporta saldos", text)
        self.assertNotIn("PDV prueba", text)
        self.assertNotIn("qty slot configurada", text)
        self.assertNotIn("Diferencia agregada", text)

    def test_dashboard_uses_query_metrics_without_recalculating_from_slots(self):
        report, _ = self.fetch()
        report["programaciones"] = []
        text = build_mensaje_premios_atrasados(report)
        self.assertIn(
            "Premios programados hasta la fecha (qty configurada): 16",
            text,
        )
        self.assertIn(
            "• 818: previstos hasta la fecha (qty configurada) 8 · entregados N/D",
            text,
        )

    def test_schedule_metrics_use_start_date_and_exclude_future_slots(self):
        payload = _payload(slot=_slot(
            start="2026-11-15", end="2026-11-20", qty=3
        ))
        slots = payload[0]["gpCoupons"][0]["storeConfig"]["stores"][0]["slots"]
        slots.extend([
            _slot(start="2026-11-17", end="2026-11-17", qty=8),
            _slot(start="2026-11-18", end="2026-11-18", qty=4),
        ])
        report, _ = self.fetch(_campaign_rows({818: payload}))
        campaign = next(row for row in report["campanias"]
                        if row["campaign_id"] == 818)
        self.assertEqual(campaign["previstos_acumulados"], 11)
        self.assertEqual(campaign["previstos_hoy"], 8)

    def test_coupon_counters_are_not_repeated_or_attributed_to_each_slot(self):
        payload = _payload()
        payload[0]["gpCoupons"][0]["storeConfig"]["stores"][0]["slots"].append(
            _slot(start="2026-11-20", end="2026-11-20", time_from="09:00", qty=5)
        )
        report, _ = self.fetch(_campaign_rows({818: payload}))
        campaign_slots = [row for row in report["programaciones"]
                          if row["campania"] == 818]
        self.assertEqual(len(campaign_slots), 2)
        self.assertEqual(
            sum(row["cantidad_slot_configurada"] for row in campaign_slots), 13
        )
        self.assertEqual(
            len([row for row in report["cupones"] if row["campania"] == 818]), 1
        )
        self.assertEqual(
            len([row for row in report["stocks_tienda"] if row["campania"] == 818]), 1
        )

    def test_global_coupon_difference_uses_configured_coupon_qty(self):
        payload = _payload()
        coupon = payload[0]["gpCoupons"][0]
        coupon["qty"] = 40
        coupon["storeConfig"]["isGlobal"] = True
        coupon["storeQty"] = {}
        report, _ = self.fetch(_campaign_rows({818: payload}))
        result = next(row for row in report["cupones"] if row["campania"] == 818)
        self.assertEqual(result["stock_total_configuracion"], 40)
        self.assertEqual(result["diferencia_agregada_cupon"], 29)

    def test_schedule_does_not_infer_current_saldo_or_deliveries(self):
        report, _ = self.fetch()
        text = build_mensaje_premios_atrasados(report)
        self.assertIn("fecha_inicio vigente", build_detalle_local(report))
        self.assertNotIn("atraso: 0", text.lower())
        self.assertNotIn("entregados: 0", text.lower())


class CommandTests(unittest.TestCase):
    def test_normal_command_prints_only_dashboard_for_all_countries(self):
        conn = _Connection(_campaign_rows())
        with mock.patch.object(
            query, "get_connection", return_value=contextlib.nullcontext(conn)
        ), mock.patch("builtins.input", return_value="n"), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = command.main([])
        output = stdout.getvalue()
        self.assertEqual(code, 0)
        for country in ("🇭🇳 HONDURAS", "🇸🇻 EL SALVADOR", "🇬🇹 GUATEMALA"):
            self.assertIn(country, output)
        for campaign_id in (828, 829, 825, 826, 818, 819, 820):
            self.assertIn(f"• {campaign_id}: previstos hasta la fecha", output)
        self.assertNotIn("SLOTS CONFIGURADOS", output)
        self.assertNotIn("PDV prueba", output)
        self.assertIn("Envío cancelado.", output)

    def test_telegram_chunks_are_split_only_between_lines(self):
        messages = command._split_telegram_messages("a" * 3000 + "\n" + "b" * 2000)
        self.assertEqual([len(message) for message in messages], [3000, 2000])
        self.assertEqual("\n".join(messages), "a" * 3000 + "\n" + "b" * 2000)

    def test_single_overlong_line_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sin partirla"):
            command._split_telegram_messages("x" * (command.TELEGRAM_LIMIT + 1))

    def test_no_send_without_explicit_affirmation(self):
        stdout = io.StringIO()
        with mock.patch.object(command, "fetch_reporte_premios_atrasados",
                               return_value={"ok": 1}), \
                mock.patch.object(command, "build_mensaje_premios_atrasados",
                                  return_value="reporte"), \
                mock.patch("builtins.input", return_value="sí"), \
                mock.patch.object(command, "send_telegram_text") as send, \
                contextlib.redirect_stdout(stdout):
            code = command.main([])
        self.assertEqual(code, 0)
        send.assert_not_called()
        self.assertIn("reporte", stdout.getvalue())

    def test_affirmative_sends_without_markdown_mode(self):
        stdout = io.StringIO()
        with mock.patch.object(command, "fetch_reporte_premios_atrasados",
                               return_value={"ok": 1}), \
                mock.patch.object(command, "build_mensaje_premios_atrasados",
                                  return_value="linea 1\nlinea 2"), \
                mock.patch("builtins.input", return_value="s"), \
                mock.patch.object(command, "send_telegram_text") as send, \
                contextlib.redirect_stdout(stdout):
            code = command.main([])
        self.assertEqual(code, 0)
        send.assert_called_once_with(command.config.notifications, "linea 1\nlinea 2")


if __name__ == "__main__":
    unittest.main()
