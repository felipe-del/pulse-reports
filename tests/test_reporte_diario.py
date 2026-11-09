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
        "premios_a_entregar_total": [58, 46, 46], "premios_a_entregar_corte": [56, 38, 45],
        "premios_entregados_hoy": [32, 29, 32],
        "total_entregados_acumulados": [100, 90, 80],
    })


class MensajeTests(unittest.TestCase):
    def test_cinco_metricas(self):
        txt = build_mensaje(_df(), _row(15, 3, 3), [])
        self.assertIn("🇬🇹 GUATEMALA\n🎯 Premios a entregar hoy: 58\n⏱️ Premios habilitados al corte: 56\n"
                      "✅ Premios entregados hoy: 32\n🔥 Premios quemados acumulados: 15\n"
                      "🏆 Total entregados acumulados: 100", txt)
        self.assertIn("🔥 Premios quemados acumulados: 3\n🏆 Total entregados acumulados: 80", txt)
        self.assertIn("Hora de corte: 05:02 p. m.", txt)
        self.assertTrue(txt.endswith("📊 Fuente: Grupo UNO · Pulse Reports"))
        self.assertNotIn("⚠️", txt)

    def test_advertencia(self):
        self.assertIn("⚠️ aviso", build_mensaje(_df(), _row(0, 0, 0), ["aviso"]))


class SqlTests(unittest.TestCase):
    def _sql(self, kind):
        return q.REPORT_SQL.format(created_date=q._CREATED_DATE[kind],
                                   created_le_corte=q._CREATED_LE_CORTE[kind],
                                   campaign_configs="(1::int, '[]'::jsonb)")

    def test_acumulado_sql(self):
        for kind in q._CREATED_DATE:
            sql = self._sql(kind)
            cte = sql.split("ganadores_acumulado AS (")[1].split("SELECT\n    ca.pais")[0]
            self.assertIn('COALESCE(SUM(cpw."totalPrizes"), 0)', cte)
            self.assertIn('GROUP BY cpw."campaignId"', cte)
            self.assertIn("<= ", cte)
            self.assertNotIn("fecha_hoy", cte)        # sin límite inferior ni fecha programada
            self.assertNotIn("horarios", cte)
            self.assertIn("818, 819, 820", cte)
            self.assertIn("825, 826", cte)
            self.assertIn("828, 829", cte)
        self.assertIn("(ct.fecha_hora_corte AT TIME ZONE 'America/Guatemala')",
                      self._sql("timestamp without time zone"))

    def test_entregados_hoy_intacto(self):
        sql = self._sql("timestamp with time zone")
        hoy = sql.split("ganadores AS (")[1].split("ganadores_acumulado")[0]
        self.assertIn("(cpw.\"createdAt\" AT TIME ZONE 'America/Guatemala')::date = ct.fecha_hoy", hoy)
        self.assertIn('cpw."createdAt" <= ct.fecha_hora_corte', hoy)

    def test_acumulado_menor_a_hoy_se_rechaza(self):
        df = _df()
        df["total_entregados_acumulados"] = [1, 90, 80]
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = ("timestamp with time zone",)
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [
            (cid, "[]") for cid, _ in q.CAMPANIAS]
        with mock.patch.object(q, "get_connection", return_value=conn), \
                mock.patch.object(q.pd, "read_sql_query", return_value=df):
            with self.assertRaises(RuntimeError):
                q.fetch_reporte_diario(mock.Mock())


class MainTests(_Tmp):
    def _run(self, data):
        path = self.write(data)
        real = load_premios_quemados
        out = io.StringIO()
        with mock.patch.object(rdp, "fetch_reporte_diario", return_value=_df()), \
                mock.patch.object(rdp, "load_premios_quemados",
                                  side_effect=lambda d: real(d, path)), \
                mock.patch.object(rdp, "send_telegram_text") as send, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = rdp.main(["--telegram"])
        return code, send

    def test_envia_con_datos_validos(self):
        code, send = self._run({"2026-11-02": _row(1, 2, 3)})
        self.assertEqual(code, 0)
        send.assert_called_once()
        self.assertIn("🔥 Premios quemados acumulados: 1", send.call_args[0][1])

    def test_no_envia_con_datos_invalidos(self):
        code, send = self._run({"2026-11-02": _row(-1, 2, 3)})
        self.assertEqual(code, 1)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
