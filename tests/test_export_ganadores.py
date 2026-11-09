import contextlib
import io
import unittest
from unittest import mock

import pandas as pd

import export_ganadores as exp
from notifications.senders import TelegramError
from reports.ganadores_validation import Check, run_checks
from queries.ganadores import CAMPAIGN_CATALOG, WinnersData, WinnersFilter

PATH = "output/interno/ganadores_test.xlsx"


def _data_879():
    camp = CAMPAIGN_CATALOG[3]
    rows = []
    for i in range(879):
        rows.append({
            "pais": camp["pais"], "campania_id": camp["campaign_id"],
            "tipo_premio": camp["premio"], "registro_ganador_id": 1000 + i,
            "cliente_id": 28278 if i == 878 else i, "nombre_completo": "x",
            "identificacion": "1", "telefono": "1", "correo_electronico": "a@b.c",
            "cantidad_premios": 0 if i == 878 else 1, "estado": 1,
            "fecha_hora_ganador": pd.Timestamp("2026-10-20 18:00:00"),
        })
    detail = pd.DataFrame(rows)
    detail.loc[878, "registro_ganador_id"] = 2189
    control = pd.DataFrame([{
        "campania_id": camp["campaign_id"], "registros": 879,
        "clientes_unicos": detail["cliente_id"].nunique(), "total_premios": 878,
        "sin_premios": 1, "primer_ganador": "2026-10-20 18:00:00",
        "ultimo_ganador": "2026-10-20 18:00:00",
    }])
    return WinnersData(detail=detail, control=control,
                       campaign_names={camp["campaign_id"]: "n"},
                       catalog=[camp], flt=WinnersFilter())


class InformativeDifferenceTests(unittest.TestCase):
    def test_879_records_878_prizes_is_non_blocking_and_detailed(self):
        data = _data_879()
        from reports.ganadores_validation import build_summary
        checks = run_checks(data, build_summary(data))
        info = [c for c in checks if not c.ok]
        self.assertEqual([c.blocking for c in info], [False])
        self.assertIn("Registros de ganadores: 879", info[0].detail)
        self.assertIn("Premios contabilizados: 878", info[0].detail)
        self.assertIn("Diferencia (registros - premios): 1", info[0].detail)
        self.assertIn("ID: 2189; Cliente: 28278; Campaña: 825; País: El Salvador; totalPrizes: 0",
                      info[0].detail)

    def test_print_checks_labels_warning_separately(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exp._print_checks([Check("a", True, "ok"),
                               Check("b", False, "info", blocking=False),
                               Check("c", False, "malo")])
        text = out.getvalue()
        self.assertIn("[OK] a", text)
        self.assertIn("[ADVERTENCIA] b", text)
        self.assertIn("[REVISAR] c", text)


class RunExportTelegramTests(unittest.TestCase):
    def _run(self, extra_checks, telegram_error=None, telegram=True):
        data = _data_879()
        args = exp._parse_args((["--telegram", "--yes"] if telegram else []) + ["--output", PATH])
        send = mock.Mock(side_effect=telegram_error, return_value={"message_id": 7})
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(exp, "fetch_winners", return_value=data),
            mock.patch.object(exp, "normalize_detail", side_effect=lambda d, _tz: d),
            mock.patch.object(exp, "build_summary", return_value=pd.DataFrame(
                {"pais": ["El Salvador"], "campania_id": [825], "tipo_premio": ["x"],
                 "registros": [879], "clientes_unicos": [1], "total_premios": [878],
                 "primer_ganador": [None], "ultimo_ganador": [None], "estados": [""]})),
            mock.patch.object(exp, "run_checks", return_value=extra_checks),
            mock.patch.object(exp, "write_workbook"),
            mock.patch.object(exp, "validate_workbook", return_value=[]),
            mock.patch.object(exp, "get_telegram_chat", return_value={"type": "group", "title": "G"}),
            mock.patch.object(exp, "send_telegram_document", send),
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
        ):
            rc = exp.run_export(args)
        return rc, out.getvalue(), err.getvalue(), send

    def test_informative_difference_still_sends(self):
        rc, out, _, send = self._run([Check("premios", False, "dif", blocking=False)])
        self.assertEqual(rc, 0)
        send.assert_called_once()
        self.assertIn("[ADVERTENCIA]", out)
        self.assertIn("[ENVIADO]", out)
        self.assertIn("Telegram: ENVIADO", out)
        self.assertIn("ganadores_test.xlsx", out)

    def test_telegram_failure_reports_not_sent_with_reason(self):
        rc, out, err, send = self._run([Check("ok", True, "")],
                                       telegram_error=TelegramError("HTTP 400 chat not found"))
        self.assertEqual(rc, 3)
        self.assertNotIn("[ENVIADO]", out)
        self.assertIn("[NO ENVIADO]", err)
        self.assertIn("chat not found", err)
        self.assertIn("Telegram: NO ENVIADO", out)

    def test_critical_failure_blocks_and_exits_nonzero(self):
        rc, out, err, send = self._run([Check("premios", False, "dif", blocking=False),
                                        Check("duplicados", False, "2 repetidos")])
        self.assertEqual(rc, 1)
        send.assert_not_called()
        self.assertIn("[NO ENVIADO]", err)
        self.assertIn("duplicados", err)
        self.assertNotIn("[ENVIADO]", out)

    def test_without_telegram_informative_difference_exits_zero(self):
        rc, _, _, send = self._run([Check("premios", False, "dif", blocking=False)],
                                   telegram=False)
        self.assertEqual(rc, 0)
        send.assert_not_called()

    def test_without_telegram_critical_failure_exits_nonzero(self):
        rc, *_ = self._run([Check("duplicados", False, "x")], telegram=False)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
