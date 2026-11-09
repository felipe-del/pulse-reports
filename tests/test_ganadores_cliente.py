import contextlib
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd

import export_ganadores_cliente as client_export
from queries.ganadores import (
    CAMPAIGN_CATALOG,
    WinnersData,
    WinnersFilter,
    _where,
    fetch_winners,
)
from reports.ganadores_cliente_excel import write_client_workbook
from reports.ganadores_cliente_validation import validate_client_workbook
from reports.ganadores_validation import Check, build_summary, country_totals, run_checks


def _sample_data(prizes=0):
    campaign = CAMPAIGN_CATALOG[3]
    stamp = datetime(2026, 10, 20, 18, 0)
    detail = pd.DataFrame([{
        "pais": campaign["pais"],
        "campania_id": campaign["campaign_id"],
        "tipo_premio": campaign["premio"],
        "nombre_campania": "Motor de premios Vehiculo",
        "registro_ganador_id": 2189,
        "cliente_id": 28278,
        "nombre_completo": "Cliente de prueba",
        "identificacion": None,
        "telefono": None,
        "correo_electronico": None,
        "cantidad_premios": prizes,
        "estado": 1,
        "fecha_hora_ganador": stamp,
        "fecha_actualizacion": stamp,
        "comercio_id": 18,
    }])
    control = pd.DataFrame([{
        "campania_id": campaign["campaign_id"],
        "registros": 1,
        "clientes_unicos": 1,
        "total_premios": prizes,
        "sin_premios": int(prizes <= 0),
        "primer_ganador": "2026-10-20 18:00:00",
        "ultimo_ganador": "2026-10-20 18:00:00",
    }])
    flt = WinnersFilter(corte_hasta=date(2026, 10, 21))
    return WinnersData(
        detail=detail,
        control=control,
        campaign_names={campaign["campaign_id"]: "Motor de premios Vehiculo"},
        catalog=[campaign],
        flt=flt,
    )


class ClientPeriodTests(unittest.TestCase):
    def test_excludes_current_day_and_includes_previous_day_last_microsecond(self):
        zone = ZoneInfo("America/Guatemala")
        local_now = datetime(2026, 10, 22, 12, 30, tzinfo=zone)
        flt = WinnersFilter(
            usar_inicio_campania=True,
            inicios_campania={825: datetime(2026, 10, 19, 0, tzinfo=zone)},
            hasta_exclusivo=datetime(2026, 10, 22, 0, tzinfo=zone),
        )
        where, params = _where(flt, "timestamp with time zone")
        self.assertIn('cpw."createdAt" < %(hasta_exclusivo)s', where)
        self.assertEqual(params["hasta_exclusivo"], local_now.replace(hour=0, minute=0))
        self.assertEqual(
            params["hasta_exclusivo"] - timedelta(microseconds=1),
            datetime(2026, 10, 21, 23, 59, 59, 999999, tzinfo=zone))

    def test_cutoff_moves_forward_when_execution_day_changes(self):
        zone = ZoneInfo("America/Guatemala")
        for today, expected in (
            (date(2026, 10, 22), date(2026, 10, 21)),
            (date(2026, 10, 23), date(2026, 10, 22)),
            (date(2026, 11, 1), date(2026, 10, 31)),
        ):
            flt = WinnersFilter()
            execution = datetime.combine(today, datetime.min.time(), tzinfo=zone)
            client_export._apply_client_period(flt, execution)
            self.assertEqual(flt.corte_hasta, expected)
            self.assertEqual(flt.hasta_exclusivo.date(), today)

    def test_manual_cutoff_can_only_narrow_automatic_cutoff(self):
        flt = WinnersFilter(hasta=date(2026, 10, 20))
        client_export._apply_client_period(
            flt, datetime(2026, 10, 22, 10, tzinfo=ZoneInfo(flt.tz)))
        where, params = _where(flt, "timestamp with time zone")
        self.assertIn('cpw."createdAt" < %(hasta)s', where)
        self.assertIn('cpw."createdAt" < %(hasta_exclusivo)s', where)
        self.assertEqual(params["hasta"], datetime(2026, 10, 21, tzinfo=ZoneInfo(flt.tz)))
        self.assertEqual(flt.corte_hasta, date(2026, 10, 20))

    def test_campaign_starts_are_applied_per_campaign(self):
        zone = ZoneInfo("America/Guatemala")
        flt = WinnersFilter(inicios_campania={
            825: datetime(2026, 10, 19, 0, tzinfo=zone),
            826: datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc),
        })
        where, params = _where(flt, "timestamp with time zone")
        self.assertIn('cpw."campaignId" = %(start_campaign_id_825)s', where)
        self.assertIn('cpw."campaignId" = %(start_campaign_id_826)s', where)
        self.assertEqual(params["start_campaign_825"], datetime(2026, 10, 19, 0, tzinfo=zone))
        self.assertEqual(
            params["start_campaign_826"],
            datetime(2026, 10, 20, 0, 30, tzinfo=zone))

    def test_detail_and_control_queries_share_the_same_period(self):
        zone = ZoneInfo("America/Guatemala")
        campaign_starts = {
            campaign["campaign_id"]: datetime(2026, 10, 19, 6, tzinfo=timezone.utc)
            for campaign in CAMPAIGN_CATALOG
        }
        flt = WinnersFilter(
            usar_inicio_campania=True,
            inicios_campania=campaign_starts,
            hasta_exclusivo=datetime(2026, 10, 22, 0, tzinfo=zone),
        )
        control = _where(flt, "timestamp with time zone")
        detail = _where(flt, "timestamp with time zone")
        self.assertEqual(detail, control)

    def test_local_timestamp_boundaries_remain_local_and_naive(self):
        flt = WinnersFilter(
            hasta_exclusivo=datetime(2026, 10, 22, 0, tzinfo=ZoneInfo("America/Guatemala")))
        where, params = _where(flt, "timestamp without time zone")
        self.assertIn('cpw."createdAt" < %(hasta_exclusivo)s', where)
        self.assertEqual(params["hasta_exclusivo"], datetime(2026, 10, 22, 0))
        self.assertIsNone(params["hasta_exclusivo"].tzinfo)


class _FakeCursor:
    def __init__(self, calls):
        self.calls = calls
        self.description = []
        self.rows = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            self.description, self.rows = [("data_type",)], [("timestamp with time zone",)]
        elif "SELECT id, begin_date" in sql:
            ids = params["ids"]
            self.description = [("id",), ("begin_date",)]
            self.rows = [(cid, datetime(2026, 10, 19, 6, tzinfo=timezone.utc)) for cid in ids]
        elif "SELECT id, name FROM campaigns" in sql:
            self.description, self.rows = [("id",), ("name",)], []
        else:
            self.description, self.rows = [], []

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConnection:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return _FakeCursor(self.calls)


class QueryAndWorkbookTests(unittest.TestCase):
    def test_fetch_applies_campaign_start_and_cutoff_to_detail_and_control(self):
        connection = _FakeConnection()

        @contextlib.contextmanager
        def fake_connection(_db):
            yield connection

        flt = WinnersFilter(
            hasta_exclusivo=datetime(2026, 10, 22, 0, tzinfo=ZoneInfo("America/Guatemala")),
            corte_hasta=date(2026, 10, 21),
            usar_inicio_campania=True,
        )
        with mock.patch("queries.ganadores.get_connection", fake_connection):
            fetch_winners(None, flt)

        detail_sql, detail_params = next(
            call for call in connection.calls if "WITH campanias" in call[0])
        control_sql, control_params = next(
            call for call in connection.calls if "COALESCE(SUM" in call[0])
        self.assertIn('cpw."createdAt" < %(hasta_exclusivo)s', detail_sql)
        self.assertIn('cpw."createdAt" < %(hasta_exclusivo)s', control_sql)
        self.assertEqual(detail_params["hasta_exclusivo"], control_params["hasta_exclusivo"])
        self.assertEqual(detail_params["start_campaign_825"], control_params["start_campaign_825"])

    def test_validation_and_excel_keep_zero_prize_winner_and_show_cutoff(self):
        data = _sample_data(prizes=0)
        summary = build_summary(data)
        checks = run_checks(data, summary)
        warning = next(c for c in checks if not c.blocking)
        self.assertFalse(warning.ok)
        self.assertIn("Registros de ganadores: 1", warning.detail)
        self.assertIn("Premios contabilizados: 0", warning.detail)
        self.assertIn("ID: 2189; Cliente: 28278; Campaña: 825; País: El Salvador; totalPrizes: 0",
                      warning.detail)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cliente.xlsx"
            write_client_workbook(
                str(path), data, datetime(2026, 10, 22, 8, tzinfo=ZoneInfo(data.flt.tz)))
            workbook_checks = validate_client_workbook(str(path), data)
            self.assertTrue(all(check.ok for check in workbook_checks))
            self.assertEqual(country_totals(data).iloc[0]["ganadores"], 1)


class TelegramDecisionTests(unittest.TestCase):
    def _run_client(self, checks, telegram=True, tty=True, answer="s"):
        data = _sample_data(prizes=0)
        args = client_export._parse_args(
            ["--telegram", "--output", "unused.xlsx"] if telegram
            else ["--output", "unused.xlsx"])
        stdin = mock.Mock()
        stdin.isatty.return_value = tty
        send = mock.Mock(return_value=0)
        prompt = mock.Mock(return_value=answer)
        with (
            mock.patch.object(client_export, "fetch_winners", return_value=data),
            mock.patch.object(client_export, "normalize_detail", side_effect=lambda d, _tz: d),
            mock.patch.object(client_export, "build_summary", return_value=pd.DataFrame()),
            mock.patch.object(client_export, "run_checks", return_value=checks),
            mock.patch.object(client_export, "write_client_workbook"),
            mock.patch.object(client_export, "validate_client_workbook", return_value=[]),
            mock.patch.object(client_export, "_send_to_telegram", send),
            mock.patch.object(client_export.sys, "stdin", stdin),
            mock.patch("builtins.input", prompt),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = client_export.run(args)
        return result, send, prompt

    def test_all_validations_allow_telegram_send(self):
        result, send, _ = self._run_client([Check("integridad", True, "ok")])
        self.assertEqual(result, 0)
        send.assert_called_once()

    def test_warning_only_requests_confirmation_and_sends_on_yes(self):
        result, send, prompt = self._run_client(
            [Check("premios", False, "observación", blocking=False)], answer="s")
        self.assertEqual(result, 0)
        prompt.assert_called_once()
        send.assert_called_once()

    def test_warning_only_cancels_on_no(self):
        result, send, prompt = self._run_client(
            [Check("premios", False, "observación", blocking=False)], answer="n")
        self.assertEqual(result, 3)
        prompt.assert_called_once()
        send.assert_not_called()

    def test_critical_failure_blocks_even_when_user_would_accept_warning(self):
        result, send, prompt = self._run_client([
            Check("premios", False, "observación", blocking=False),
            Check("integridad", False, "duplicados"),
        ], answer="s")
        self.assertEqual(result, 1)
        prompt.assert_not_called()
        send.assert_not_called()

    def test_warning_without_interactive_input_never_sends(self):
        result, send, prompt = self._run_client(
            [Check("premios", False, "observación", blocking=False)], tty=False)
        self.assertEqual(result, 3)
        prompt.assert_not_called()
        send.assert_not_called()

    def test_without_telegram_generates_report_without_prompt_on_warning(self):
        result, send, prompt = self._run_client(
            [Check("premios", False, "observación", blocking=False)],
            telegram=False, tty=False)
        self.assertEqual(result, 0)
        prompt.assert_not_called()
        send.assert_not_called()

    def test_console_warning_lists_affected_record_details(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            client_export._print_client_checks([
                Check(
                    "totalPrizes vs registros (informativo)",
                    False,
                    "Registros de ganadores: 154\nPremios contabilizados: 153\n"
                    "ID: 2189; Cliente: 28278; Campaña: 825; País: El Salvador; totalPrizes: 0",
                    blocking=False,
                )
            ])
        text = output.getvalue()
        self.assertIn("[AVISO] Cantidad de premios", text)
        self.assertIn("ID: 2189", text)
        self.assertIn("Cliente: 28278", text)
        self.assertIn("Campaña: 825", text)
        self.assertIn("País: El Salvador", text)
        self.assertIn("totalPrizes: 0", text)


if __name__ == "__main__":
    unittest.main()
