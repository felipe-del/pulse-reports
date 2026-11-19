import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

import reporte_diario_premios as rdp
from queries import reporte_diario_premios as q
from queries.premios_quemados import PremiosQuemadosError, load_premios_quemados
from reports.reporte_diario_mensaje import build_mensaje
from diagnostico_acumulado_configuracion import summarize_config_rows

P = ("Guatemala", "El Salvador", "Honduras")


def _row(a, b, c):
    return dict(zip(P, (a, b, c)))


class _Tmp(unittest.TestCase):
    def write(self, data):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "q.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path


class QuemadosTests(_Tmp):
    def test_suma_varias_fechas_y_sin_advertencia(self):
        path = self.write({"2026-11-01": _row(10, 1, 0), "2026-11-02": _row(5, 2, 3)})
        totals, warns = load_premios_quemados(date(2026, 11, 2), path)
        self.assertEqual(totals, _row(15, 3, 3))
        self.assertEqual(warns, [])

    def test_falta_hoy_conserva_historico_con_advertencia(self):
        path = self.write({"2026-11-01": _row(10, 1, 0)})
        totals, warns = load_premios_quemados(date(2026, 11, 2), path)
        self.assertEqual(totals, _row(10, 1, 0))
        self.assertEqual(len(warns), 1)

    def test_fechas_futuras_no_cuentan(self):
        path = self.write({"2026-11-01": _row(1, 1, 1), "2026-11-05": _row(9, 9, 9)})
        self.assertEqual(load_premios_quemados(date(2026, 11, 2), path)[0], _row(1, 1, 1))

    def test_invalidos(self):
        bad = [
            "{no json", "[]", {},
            {"2026-11-02": _row(-1, 0, 0)},
            {"2026-11-02": _row(1.5, 0, 0)},
            {"2026-11-02": _row("3", 0, 0)},
            {"2026-11-02": _row(True, 0, 0)},
            {"2026-11-02": {"Guatemala": 1, "El Salvador": 1}},
            {"2026-11-02": {**_row(1, 1, 1), "Panama": 1}},
            {"02/11/2026": _row(1, 1, 1)},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(PremiosQuemadosError):
                load_premios_quemados(date(2026, 11, 2), self.write(data))

    def test_utf8_normal_y_con_bom(self):
        content = json.dumps({"2026-11-02": _row(477, 458, 411)}, indent=2)
        for enc in ("utf-8", "utf-8-sig"):
            with self.subTest(enc=enc):
                d = tempfile.TemporaryDirectory()
                self.addCleanup(d.cleanup)
                path = Path(d.name) / "q.json"
                path.write_bytes(content.encode(enc))
                totals, warns = load_premios_quemados(date(2026, 11, 2), path)
                self.assertEqual(totals, _row(477, 458, 411))
                self.assertEqual(warns, [])

    def test_archivo_inexistente(self):
        with self.assertRaises(PremiosQuemadosError):
            load_premios_quemados(date(2026, 11, 2), Path("no_existe.json"))

    def test_idempotente_y_no_modifica_archivo(self):
        path = self.write({"2026-11-01": _row(10, 0, 0), "2026-11-02": _row(5, 0, 0)})
        before = path.read_bytes()
        r1 = load_premios_quemados(date(2026, 11, 2), path)
        r2 = load_premios_quemados(date(2026, 11, 2), path)
        self.assertEqual(r1, r2)
        self.assertEqual(path.read_bytes(), before)


def _df():
    return pd.DataFrame({
        "pais": list(P), "fecha": [date(2026, 11, 2)] * 3, "hora_corte": ["05:02 PM"] * 3,
        "premios_adjudicados_hoy": [32, 29, 32],
        "premios_adjudicados_acumulados": [100, 90, 80],
        "programado_hoy_config_vigente": [58, 46, 46],
        "programados_nuevos_hoy": [50, 35, 39],
        "programados_arrastrados_hoy": [8, 11, 7],
        "arrastrados_por_fecha_inicio": [
            {date(2026, 10, 30): 8},
            {date(2026, 11, 1): 11},
            {date(2026, 10, 31): 7},
        ],
        "programados_vigentes_fin_posterior_hoy": [3, 5, 2],
        "programado_acumulado_config_vigente": [56, 38, 45],
    })


class MensajeTests(unittest.TestCase):
    def test_mensaje_breve_solo_muestra_metricas_calculables(self):
        txt = build_mensaje(_df())
        self.assertIn("🇬🇹 GUATEMALA\n🎯 Programado para hoy: 58", txt)
        self.assertIn("🆕 Nuevos: 50", txt)
        self.assertIn("🔁 Programados arrastrados: 8 (inicio original: 30/10/2026: 8)", txt)
        self.assertIn(
            "📅 Vigentes hoy, fin posterior (fuera del total): 3",
            txt,
        )
        self.assertIn("📈 Premios programados acumulados al cierre de hoy: 56", txt)
        self.assertIn("✅ Premios adjudicados hoy al corte: 32", txt)
        self.assertIn("🏆 Premios adjudicados acumulados al corte (incluye hoy): 100", txt)
        self.assertIn("Hora de corte: 05:02 p. m.", txt)
        self.assertNotIn("qty", txt)
        self.assertNotIn("slot", txt)
        self.assertNotIn("adjudicaciones sin vínculo", txt)

    def test_preview_rollover_origins_are_before_report_day(self):
        report_day = pd.to_datetime(_df().iloc[0]["fecha"]).date()
        for breakdown in _df()["arrastrados_por_fecha_inicio"]:
            for origin in breakdown:
                self.assertLess(origin, report_day)

    def test_later_end_line_is_hidden_when_zero(self):
        df = _df()
        df["programados_vigentes_fin_posterior_hoy"] = [0, 0, 0]
        txt = build_mensaje(df)
        self.assertNotIn("Vigentes hoy, fin posterior", txt)

def _payload(slots):
    return [{
        "gpCoupons": [{
            "isWinner": True,
            "storeConfig": {"stores": [{"slots": slots}]},
        }]
    }]


class ScheduleTests(unittest.TestCase):
    cutoff_date = date(2026, 11, 12)

    def test_new_and_rollover_slots_group_by_date_from_with_end_today(self):
        config = _payload([
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-12", "timeFrom": "09:00", "qty": 35},
            {"dateFrom": "2026-11-10", "dateTo": "2026-11-12", "timeFrom": "09:00", "qty": 4},
        ])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_hoy_config_vigente"], 39)
        self.assertEqual(
            total["programado_hoy_config_vigente"],
            total["programados_nuevos_hoy"] + total["programados_arrastrados_hoy"],
        )
        self.assertEqual(total["programados_nuevos_hoy"], 35)
        self.assertEqual(total["programados_arrastrados_hoy"], 4)
        self.assertEqual(total["arrastrados_por_fecha_inicio"], {date(2026, 11, 10): 4})
        self.assertEqual(total["programado_acumulado_config_vigente"], 39)

    def test_programmed_cumulative_does_not_depend_on_adjudication_totals(self):
        config = _payload([{
            "dateFrom": "2026-11-12", "dateTo": "2026-11-12",
            "timeFrom": "09:00", "qty": 5,
        }])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_acumulado_config_vigente"], 5)

    def test_excess_adjudications_do_not_change_configured_programming(self):
        config = _payload([
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-12", "timeFrom": "09:00", "qty": 1},
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-12", "timeFrom": "09:00", "qty": 2},
        ])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_acumulado_config_vigente"], 3)

    def test_date_extension_uses_current_configuration_not_historical_origin(self):
        config = _payload([{
            "dateFrom": "2026-11-10",
            "dateTo": "2026-11-12",
            "timeFrom": "09:00",
            "qty": 7,
        }])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_hoy_config_vigente"], 7)
        self.assertEqual(total["programados_arrastrados_hoy"], 7)
        self.assertEqual(total["arrastrados_por_fecha_inicio"], {date(2026, 11, 10): 7})
        self.assertEqual(total["programado_acumulado_config_vigente"], 7)

    def test_cumulative_includes_all_slots_starting_today_despite_time(self):
        config = _payload([
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-12", "timeFrom": "10:00", "qty": 2},
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-12", "timeFrom": "10:01", "qty": 4},
        ])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_hoy_config_vigente"], 6)
        self.assertEqual(total["programado_acumulado_config_vigente"], 6)

    def test_programs_ending_after_today_are_separate_from_today_total(self):
        config = _payload([
            {"dateFrom": "2026-11-10", "dateTo": "2026-11-15", "timeFrom": "08:00", "qty": 6},
            {"dateFrom": "2026-11-12", "dateTo": "2026-11-14", "timeFrom": "11:00", "qty": 3},
        ])
        total = q._schedule_totals({818: config}, self.cutoff_date)[818]
        self.assertEqual(total["programado_hoy_config_vigente"], 0)
        self.assertEqual(total["programados_vigentes_fin_posterior_hoy"], 9)
        self.assertEqual(total["programado_acumulado_config_vigente"], 9)

    def test_missing_date_from_is_not_replaced_by_date_to(self):
        config = _payload([{"dateTo": "2026-11-12", "timeFrom": "09:00", "qty": 4}])
        with self.assertRaises(q.CampaignConfigurationError):
            q._decode_campaign_payload(825, config)

    def test_invalid_date_range_is_rejected(self):
        config = _payload([{
            "dateFrom": "2026-11-13", "dateTo": "2026-11-12",
            "timeFrom": "09:00", "qty": 4,
        }])
        with self.assertRaises(q.CampaignConfigurationError):
            q._decode_campaign_payload(825, config)

    def test_duplicate_campaign_configuration_is_counted_once(self):
        payload = _payload([{
            "dateFrom": "2026-11-12", "dateTo": "2026-11-12",
            "timeFrom": "10:00", "qty": 2,
        }])
        configs = q._unique_campaign_configs([(818, payload), (818, payload)])
        self.assertEqual(len(configs), 1)
        self.assertEqual(
            q._schedule_totals(configs, self.cutoff_date)[818]
            ["programado_acumulado_config_vigente"],
            2,
        )

    def test_conflicting_duplicate_campaign_configs_remain_unavailable(self):
        first = _payload([{
            "dateFrom": "2026-11-12", "dateTo": "2026-11-12",
            "timeFrom": "10:00", "qty": 2,
        }])
        second = _payload([{
            "dateFrom": "2026-11-12", "dateTo": "2026-11-12",
            "timeFrom": "10:00", "qty": 3,
        }])
        configs = q._unique_campaign_configs([(818, first), (818, second)])
        self.assertIsNone(configs[818])
        totals = q._schedule_totals(configs, self.cutoff_date)
        self.assertIsNone(totals[818]["programado_acumulado_config_vigente"])

    def test_invalid_campaign_config_is_marked_unavailable(self):
        configs = q._unique_campaign_configs([(818, "{invalid json")])
        self.assertIsNone(configs[818])

    def test_comparison_counts_extended_schedule_once_by_start_date(self):
        payload = _payload([{
            "dateFrom": "2026-11-10", "dateTo": "2026-11-15",
            "timeFrom": "09:00", "qty": 4,
        }])
        comparison = summarize_config_rows([(825, payload)])[825]
        self.assertEqual(comparison["date_to_qty"], 0)
        self.assertEqual(comparison["date_from_qty"], 4)
        self.assertEqual(comparison["date_from_only_qty"], 4)
        self.assertEqual(comparison["winning_slots"], 1)

    def test_comparison_includes_all_of_today_for_date_from_method(self):
        payload = _payload([{
            "dateFrom": "2026-11-12", "dateTo": "2026-11-15",
            "timeFrom": "10:27", "qty": 4,
        }])
        comparison = summarize_config_rows([(826, payload)])[826]
        self.assertEqual(comparison["date_to_qty"], 0)
        self.assertEqual(comparison["date_from_qty"], 4)
        self.assertEqual(comparison["date_from_only_qty"], 4)

    def test_comparison_does_not_deduplicate_repeated_configuration_rows(self):
        payload = _payload([{
            "dateFrom": "2026-11-10", "dateTo": "2026-11-15",
            "timeFrom": "09:00", "qty": 4,
        }])
        comparison = summarize_config_rows([(825, payload), (825, payload)])[825]
        self.assertEqual(comparison["configuration_rows"], 2)
        self.assertEqual(comparison["date_from_qty"], 8)


class SqlTests(unittest.TestCase):
    def _sql(self, kind):
        return q.REPORT_SQL.format(created_date=q._CREATED_DATE[kind],
                                   created_le_corte=q._CREATED_LE_CORTE[kind])

    def test_acumulado_sql(self):
        for kind in q._CREATED_DATE:
            sql = self._sql(kind)
            cte = sql.split("ganadores_acumulado AS (")[1].split("SELECT\n    ca.pais")[0]
            self.assertIn('COALESCE(SUM(cpw."totalPrizes"), 0)', cte)
            self.assertIn('GROUP BY cpw."campaignId"', cte)
            self.assertIn("<= ", cte)
            self.assertNotIn("horarios", cte)
            self.assertIn("818, 819, 820", cte)
            self.assertIn("825, 826", cte)
            self.assertIn("828, 829", cte)
        self.assertIn("(ct.fecha_hora_corte AT TIME ZONE 'America/Guatemala')",
                      self._sql("timestamp without time zone"))

    def test_adjudicaciones_hoy_usan_el_mismo_corte(self):
        sql = self._sql("timestamp with time zone")
        hoy = sql.split("ganadores AS (")[1].split("ganadores_acumulado")[0]
        self.assertIn("(cpw.\"createdAt\" AT TIME ZONE 'America/Guatemala')::date = ct.fecha_hoy", hoy)
        self.assertIn('cpw."createdAt" <= ct.fecha_hora_corte', hoy)
        self.assertIn("%(fecha_hora_corte)s::timestamptz", sql)

    def test_no_asigna_adjudicaciones_a_slots(self):
        sql = self._sql("timestamp with time zone")
        self.assertNotIn("slot", sql.lower())
        self.assertNotIn("qty", sql.lower())
        self.assertIn("AS premios_adjudicados_hoy", sql)
        self.assertIn("AS premios_adjudicados_acumulados", sql)

    def test_acumulado_incluye_adjudicaciones_de_hoy(self):
        df = _df()
        df.loc[0, "premios_adjudicados_acumulados"] = 1
        with self.assertRaisesRegex(RuntimeError, "acumulados < adjudicados hoy"):
            q._validate_adjudication_totals(df)


class MainTests(_Tmp):
    def test_comando_normal_conserva_confirmacion_y_cancelacion(self):
        out = io.StringIO()
        with mock.patch.object(rdp, "fetch_reporte_diario", return_value=_df()), \
                mock.patch.object(rdp, "send_telegram_text") as send, \
                mock.patch("builtins.input", return_value="n") as prompt, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = rdp.main([])
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        send.assert_not_called()
        self.assertIn("Envío cancelado.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
