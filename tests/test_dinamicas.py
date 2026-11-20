import contextlib
import io
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import load_workbook

from config.dinamicas import DynamicsConfigError, load_dynamics, validate_config
from queries.dinamicas import (
    SOURCE_REQUIREMENTS,
    DynamicsData,
    _period_clause,
    _query_stories,
    fetch_dynamics,
)
from reports.dinamicas_excel import write_workbook
from reports.dinamicas_validation import Check, build_metrics, information_state, run_checks


def _raw_config(variants=2):
    rows = []
    for index in range(variants):
        rows.append({
            "codigo": f"VAR{index + 1}",
            "nombre": f"Variante {index + 1}",
            "historia_id": 100 + index,
            "campania_raspadita_id": 200 + index,
            "reto_id": 300 + index,
            "cupon_id": 400 + index,
            "eventos_vista": None,
            "eventos_cta": None,
        })
    return {
        "version": 1,
        "dinamica_activa": "prueba",
        "dinamicas": {
            "prueba": {
                "nombre": "Dinámica de prueba",
                "descripcion": "Configuración simulada",
                "pais": "Honduras",
                "zona_horaria": "America/Tegucigalpa",
                "fecha_inicio": None,
                "fecha_fin": None,
                "variantes": rows,
            },
            "otra": {
                "nombre": "Otra",
                "descripcion": "Dinamica secundaria",
                "pais": "Honduras",
                "zona_horaria": "America/Tegucigalpa",
                "fecha_inicio": None,
                "fecha_fin": None,
                "variantes": rows[:1],
            },
        },
    }


def _dynamics(variants=2, raw=None, selected=None):
    return validate_config(raw or _raw_config(variants), selected=selected)


def _schema():
    schema = {table: {column: "text" for column in columns}
              for table, columns in {
                  table: set().union(*[
                      requirements.get(table, set())
                      for requirements in SOURCE_REQUIREMENTS.values()])
                  for table in set().union(*[
                      set(requirements) for requirements in SOURCE_REQUIREMENTS.values()])
              }.items()}
    for table in ("stories", "campaigns", "challenges", "rewards"):
        schema.setdefault(table, {})["id"] = "integer"
    for table in ("campaigncustomer", "challengecustomer", "challengecustomerrecords",
                  "customerrewards"):
        schema[table]["createdAt"] = "timestamp with time zone"
    return schema


def _empty_data(dynamics=None):
    dynamics = dynamics or _dynamics()
    story = pd.DataFrame([
        {
            "historia_id": variant["historia_id"], "es_total": 0,
            "historias_asignadas": 0, "usuarios_asignados": 0,
            "total_vistas": 0, "usuarios_unicos_vistas": 0,
            "total_cta": 0, "usuarios_cta": 0, "acciones_registradas": 0,
        }
        for variant in dynamics.variantes
    ] + [{
        "historia_id": None, "es_total": 1, "historias_asignadas": 0,
        "usuarios_asignados": 0, "total_vistas": 0, "usuarios_unicos_vistas": 0,
        "total_cta": 0, "usuarios_cta": 0, "acciones_registradas": 0,
    }])
    return DynamicsData(
        dinamica=dynamics,
        generated_at=datetime(2026, 11, 19, 12, 0, tzinfo=ZoneInfo("America/Tegucigalpa")),
        periodo_desde=None,
        periodo_hasta=None,
        story_summary=story,
        story_action_descriptions={},
        scratch=pd.DataFrame(columns=[
            "registro_id", "cliente_id", "campania_id", "resultado", "fecha_registro",
            "nombre", "apellido", "correo", "telefono"]),
        challenges=pd.DataFrame(columns=[
            "reto_id", "nombre_reto", "aceptacion_id", "cliente_id",
            "fecha_aceptacion", "nombre", "apellido", "correo",
            "cantidad_registros_cumplimiento", "fecha_cumplimiento", "completado"]),
        coupons=pd.DataFrame(columns=[
            "asignacion_id", "cliente_id", "cupon_id", "nombre_cupon",
            "fecha_asignacion", "estado", "nombre", "apellido", "correo", "telefono"]),
        source_status={source: "Disponible" for source in SOURCE_REQUIREMENTS},
        schema=_schema(),
    )


def _filled_data():
    data = _empty_data()
    data.scratch = pd.DataFrame([
        {"registro_id": 1, "cliente_id": 10, "campania_id": 200, "resultado": 1,
         "fecha_registro": datetime(2026, 11, 10), "nombre": "A", "apellido": "A",
         "correo": None, "telefono": None},
        {"registro_id": 2, "cliente_id": 10, "campania_id": 200, "resultado": 0,
         "fecha_registro": datetime(2026, 11, 11), "nombre": "A", "apellido": "A",
         "correo": None, "telefono": None},
        {"registro_id": 3, "cliente_id": 12, "campania_id": 200, "resultado": None,
         "fecha_registro": datetime(2026, 11, 12), "nombre": "B", "apellido": "B",
         "correo": None, "telefono": None},
        {"registro_id": 4, "cliente_id": 10, "campania_id": 201, "resultado": 1,
         "fecha_registro": datetime(2026, 11, 13), "nombre": None, "apellido": None,
         "correo": None, "telefono": None},
    ])
    data.challenges = pd.DataFrame([
        {"reto_id": 300, "nombre_reto": "Reto", "aceptacion_id": 1, "cliente_id": 20,
         "fecha_aceptacion": datetime(2026, 11, 1), "nombre": "C", "apellido": "C",
         "correo": None, "cantidad_registros_cumplimiento": 2,
         "fecha_cumplimiento": datetime(2026, 11, 3), "completado": True},
        {"reto_id": 300, "nombre_reto": "Reto", "aceptacion_id": 2, "cliente_id": 20,
         "fecha_aceptacion": datetime(2026, 11, 2), "nombre": "C", "apellido": "C",
         "correo": None, "cantidad_registros_cumplimiento": 2,
         "fecha_cumplimiento": datetime(2026, 11, 3), "completado": True},
        {"reto_id": 300, "nombre_reto": "Reto", "aceptacion_id": 3, "cliente_id": 21,
         "fecha_aceptacion": datetime(2026, 11, 2), "nombre": "D", "apellido": "D",
         "correo": None, "cantidad_registros_cumplimiento": 0,
         "fecha_cumplimiento": None, "completado": False},
    ])
    data.coupons = pd.DataFrame([
        {"asignacion_id": 11, "cliente_id": 30, "cupon_id": 400,
         "nombre_cupon": "Entrada", "fecha_asignacion": datetime(2026, 11, 1),
         "estado": 0, "nombre": "E", "apellido": "E", "correo": None, "telefono": None},
        {"asignacion_id": 12, "cliente_id": 30, "cupon_id": 400,
         "nombre_cupon": "Entrada", "fecha_asignacion": datetime(2026, 11, 2),
         "estado": 1, "nombre": "E", "apellido": "E", "correo": None, "telefono": None},
        {"asignacion_id": 13, "cliente_id": 32, "cupon_id": 400,
         "nombre_cupon": "Entrada", "fecha_asignacion": datetime(2026, 11, 3),
         "estado": 2, "nombre": "F", "apellido": "F", "correo": None, "telefono": None},
        {"asignacion_id": 14, "cliente_id": 33, "cupon_id": 401,
         "nombre_cupon": "Entrada", "fecha_asignacion": datetime(2026, 11, 3),
         "estado": -1, "nombre": "G", "apellido": "G", "correo": None, "telefono": None},
    ])
    return data


class ConfigurationTests(unittest.TestCase):
    def test_selects_active_and_alternate_dynamic(self):
        self.assertEqual(_dynamics().key, "prueba")
        self.assertEqual(_dynamics(raw=_raw_config(), selected="otra").key, "otra")

    def test_rejects_bad_ids_codes_dates_timezone_and_missing_dynamic(self):
        cases = []
        raw = _raw_config()
        raw["dinamicas"]["prueba"]["variantes"][0]["historia_id"] = True
        cases.append(raw)
        raw = _raw_config()
        raw["dinamicas"]["prueba"]["variantes"][1]["codigo"] = "VAR1"
        cases.append(raw)
        raw = _raw_config()
        raw["dinamicas"]["prueba"]["fecha_inicio"] = "2026-02-30"
        cases.append(raw)
        raw = _raw_config()
        raw["dinamicas"]["prueba"]["fecha_inicio"] = "2026-12-01"
        raw["dinamicas"]["prueba"]["fecha_fin"] = "2026-11-30"
        cases.append(raw)
        raw = _raw_config()
        raw["dinamicas"]["prueba"]["zona_horaria"] = "America/NotAZone"
        cases.append(raw)
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(DynamicsConfigError):
                    validate_config(invalid)
        with self.assertRaises(DynamicsConfigError):
            validate_config(_raw_config(), selected="missing")

    def test_two_or_more_variants_are_supported(self):
        self.assertEqual(len(_dynamics(variants=3).variantes), 3)

    def test_invalid_json_file_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.json"
            path.write_text("{ invalid", encoding="utf-8")
            with self.assertRaises(DynamicsConfigError):
                load_dynamics(path)


class MetricsAndValidationTests(unittest.TestCase):
    def test_empty_sources_produce_zero_for_verified_sources_and_nd_for_cta(self):
        data = _empty_data()
        variants, total = build_metrics(data)
        self.assertEqual(len(variants), 2)
        self.assertEqual(total["raspadita_total"], 0)
        self.assertEqual(total["reto_aceptaciones"], 0)
        self.assertEqual(total["cupones_asignados"], 0)
        self.assertEqual(total["historias_vistas"], 0)
        self.assertIsNone(total["historias_usuarios_cta"])
        self.assertEqual(information_state(data).split(":")[0], "PARCIAL")

    def test_multiple_scratch_records_keep_row_and_unique_counts_separate(self):
        variants, total = build_metrics(_filled_data())
        first = variants[0]
        self.assertEqual(first["raspadita_total"], 3)
        self.assertEqual(first["raspadita_usuarios"], 2)
        self.assertEqual(first["raspadita_ganadores"], 1)
        self.assertEqual(first["raspadita_perdedores"], 1)
        self.assertEqual(first["raspadita_sin_resultado"], 1)
        self.assertEqual(first["raspadita_usuarios_ganadores"], 1)
        self.assertEqual(total["raspadita_total"], 4)
        self.assertEqual(total["raspadita_usuarios"], 2)

    def test_challenges_without_completion_are_pending_and_completion_not_multiplied(self):
        variants, total = build_metrics(_filled_data())
        first = variants[0]
        self.assertEqual(first["reto_aceptaciones"], 3)
        self.assertEqual(first["reto_usuarios_aceptaron"], 2)
        self.assertEqual(first["reto_usuarios_completaron"], 1)
        self.assertEqual(first["reto_usuarios_pendientes"], 1)
        self.assertEqual(first["reto_registros_cumplimiento"], 2)
        self.assertEqual(total["reto_registros_cumplimiento"], 2)

    def test_each_coupon_state_is_counted_and_unique_customers_are_distinct(self):
        variants, total = build_metrics(_filled_data())
        first = variants[0]
        self.assertEqual(first["cupones_asignados"], 3)
        self.assertEqual(first["cupones_clientes"], 2)
        self.assertEqual(first["cupones_canjeados"], 1)
        self.assertEqual(first["cupones_sin_canjear"], 1)
        self.assertEqual(first["cupones_vencidos"], 1)
        self.assertEqual(first["cupones_cancelados"], 0)
        self.assertEqual(total["cupones_asignados"], 4)
        self.assertEqual(total["cupones_cancelados"], 1)

    def test_integrity_checks_flag_unexpected_result_and_missing_customer(self):
        data = _filled_data()
        data.scratch.loc[0, "resultado"] = 7
        data.scratch.loc[0, "cliente_id"] = None
        checks = run_checks(data, build_metrics(data))
        self.assertFalse(next(c for c in checks if c.name == "Raspaditas: total = categorías").ok)
        self.assertFalse(next(c for c in checks if c.name == "Raspaditas: valores de resultado esperados").ok)
        self.assertFalse(next(c for c in checks if c.name == "Raspaditas: registros sin cliente").ok)

    def test_dashboard_counts_reconcile_to_exported_details(self):
        checks = run_checks(_filled_data(), build_metrics(_filled_data()))
        for name in ("Dashboard raspadita = detalle", "Dashboard retos = detalle",
                     "Dashboard cupones = detalle"):
            self.assertTrue(next(c for c in checks if c.name == name).ok, name)

    def test_cta_requires_a_description_observed_for_the_same_story(self):
        raw = _raw_config(variants=1)
        raw["dinamicas"]["prueba"]["variantes"][0]["eventos_cta"] = ["tap"]
        dynamics = _dynamics(raw=raw)
        data = _empty_data(dynamics)
        metrics = build_metrics(data)
        self.assertIsNone(metrics[0][0]["historias_usuarios_cta"])
        data.story_action_descriptions = {100: {"tap"}}
        metrics = build_metrics(data)
        self.assertEqual(metrics[0][0]["historias_usuarios_cta"], 0)
        self.assertIsNone(metrics[0][0]["historias_pct_cta"])

    def test_story_query_avoids_grouping_sets_and_groups_configuration_fields(self):
        data = _empty_data()
        with mock.patch("queries.dinamicas._read") as read:
            _query_stories(object(), data.dinamica)
        sql = read.call_args.args[1]
        self.assertNotIn("GROUPING SETS", sql)
        self.assertIn("GROUP BY story_id, view_events, cta_events", sql)
        self.assertIn("COUNT(action_customer) FILTER", sql)
        self.assertIn("COUNT(DISTINCT action_customer) FILTER", sql)
        self.assertIn("UNION ALL", sql)

    def test_timezone_boundaries_are_local_and_timestamp_type_aware(self):
        where, params = _period_clause(
            'cc."createdAt"', date(2026, 11, 1), date(2026, 11, 2),
            "America/Tegucigalpa", "timestamp with time zone")
        self.assertEqual(where, ' AND cc."createdAt" >= %(desde)s AND cc."createdAt" < %(hasta)s')
        self.assertEqual(params["desde"].hour, 0)
        self.assertEqual(params["desde"].utcoffset().total_seconds(), -21600)
        self.assertEqual(params["hasta"].date(), date(2026, 11, 3))
        _, naive = _period_clause(
            "createdAt", date(2026, 11, 1), None,
            "America/Tegucigalpa", "timestamp without time zone")
        self.assertIsNone(naive["desde"].tzinfo)


class _FakeCursor:
    def __init__(self, schema, rows_by_source, id_rows):
        self.schema = schema
        self.rows_by_source = rows_by_source
        self.id_rows = id_rows
        self.description = []
        self.rows = []
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            self.description = [("table_name",), ("column_name",), ("data_type",)]
            self.rows = [
                (table, column, kind)
                for table, columns in self.schema.items()
                for column, kind in columns.items()
                if table in params[0]
            ]
        elif sql.strip() == "SELECT 1 AS ok;":
            self.description, self.rows = [("ok",)], [(1,)]
        elif "SELECT id FROM " in sql:
            table = sql.split("SELECT id FROM ", 1)[1].split(" ", 1)[0]
            self.description, self.rows = [("id",)], [
                (value,) for value in self.id_rows.get(table, [])
                if value in params[0]
            ]
        elif "WITH configured(story_id" in sql:
            self.description = [
                ("historia_id",), ("es_total",), ("historias_asignadas",),
                ("usuarios_asignados",), ("total_vistas",),
                ("usuarios_unicos_vistas",), ("total_cta",), ("usuarios_cta",),
                ("acciones_registradas",),
            ]
            indices = [int(key[5:]) for key in params
                       if key.startswith("story") and key[5:].isdigit()]
            ids = [params[f"story{i}"] for i in sorted(indices)]
            self.rows = [
                (story_id, 0, 0, 0, 0, 0, 0, 0, 0) for story_id in ids
            ] + [(None, 1, 0, 0, 0, 0, 0, 0, 0)]
        elif "SELECT DISTINCT sc.story" in sql:
            self.description, self.rows = [("historia_id",), ("description",)], []
        elif "FROM campaigncustomer cc" in sql:
            self.description, self.rows = self.rows_by_source["raspaditas"]
        elif "WITH cumplimiento AS" in sql:
            self.description, self.rows = self.rows_by_source["retos"]
        elif "FROM customerrewards cr" in sql:
            self.description, self.rows = self.rows_by_source["cupones"]
        elif "FROM story_actions" in sql:
            self.description = [("description",), ("registros",)]
            self.rows = [("view", 12), ("open", 2)]
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConnection:
    def __init__(self, schema=None, rows_by_source=None, id_rows=None):
        self.cursor_instance = _FakeCursor(schema or _schema(), rows_by_source or {}, id_rows or {})

    def cursor(self):
        return self.cursor_instance


def _rows_by_source():
    rows = {}
    fixtures = {
        "raspaditas": [
            "registro_id", "cliente_id", "campania_id", "resultado", "fecha_registro",
            "nombre", "apellido", "correo", "telefono"],
        "retos": [
            "reto_id", "nombre_reto", "aceptacion_id", "cliente_id", "fecha_aceptacion",
            "nombre", "apellido", "correo", "cantidad_registros_cumplimiento",
            "fecha_cumplimiento", "completado"],
        "cupones": [
            "asignacion_id", "cliente_id", "cupon_id", "nombre_cupon", "fecha_asignacion",
            "estado", "nombre", "apellido", "correo", "telefono"],
    }
    for source, columns in fixtures.items():
        rows[source] = ([(column,) for column in columns], [])
    return rows


class SimulatedIntegrationTests(unittest.TestCase):
    def _fetch(self, dynamics=None, **kwargs):
        dynamics = dynamics or _dynamics()
        ids = {
            "stories": [v["historia_id"] for v in dynamics.variantes],
            "campaigns": [v["campania_raspadita_id"] for v in dynamics.variantes],
            "challenges": [v["reto_id"] for v in dynamics.variantes],
            "rewards": [v["cupon_id"] for v in dynamics.variantes],
        }
        connection = _FakeConnection(rows_by_source=_rows_by_source(), id_rows=ids)

        @contextlib.contextmanager
        def fake_connection(_db):
            yield connection

        with mock.patch("queries.dinamicas.get_connection", fake_connection):
            data = fetch_dynamics(
                None, dynamics,
                datetime(2026, 11, 19, 12, tzinfo=ZoneInfo(dynamics.zona_horaria)),
                **kwargs)
        return data, connection

    def test_empty_simulated_db_generates_all_sheets_and_zero_metrics(self):
        data, _ = self._fetch()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.xlsx"
            write_workbook(str(path), data, run_checks(data, build_metrics(data)))
            workbook = load_workbook(path, data_only=True)
            self.assertEqual(workbook.sheetnames, [
                "DASHBOARD", "PARTICIPANTES RASPADITA", "GANADORES", "PERDEDORES",
                "RETOS", "CUPONES"])
            dashboard = workbook["DASHBOARD"]
            values = [cell.value for row in dashboard.iter_rows() for cell in row]
            self.assertIn(0, values)
            self.assertIn("N/D", values)
            self.assertNotIn("Estado de la información", values)
            self.assertEqual(dashboard["A7"].value, "Indicador")
            self.assertEqual(dashboard["A11"].value, "RASPÁDITAS")
            self.assertEqual(dashboard["A12"].value, "Total de registros")
            self.assertEqual(dashboard["A13"].value, "Participantes únicos")
            self.assertNotIn("Historias asignadas a clientes", values)
            self.assertNotIn("Llamadas a la acción ejecutadas", values)
            self.assertEqual(len(dashboard._charts), 1)
            chart = dashboard._charts[0]
            self.assertEqual(len(chart.series), 3)
            self.assertEqual(chart.anchor._from.col, 4)
            self.assertEqual(chart.anchor._from.row, 6)
            self.assertEqual(chart.anchor.ext.cx, 12 * 360000)
            self.assertEqual(chart.anchor.ext.cy, 6 * 360000)
            self.assertEqual(
                [series.val.numRef.f for series in chart.series],
                ["'DASHBOARD'!$B$12:$C$12", "'DASHBOARD'!$B$14:$C$14",
                 "'DASHBOARD'!$B$15:$C$15"])
            self.assertEqual(chart.series[0].cat.numRef.f, "'DASHBOARD'!$B$7:$C$7")
            self.assertEqual(workbook["PARTICIPANTES RASPADITA"].max_row, 4)
            workbook.close()

    def test_populated_simulated_data_exports_filtered_detail_and_real_winners(self):
        data = _filled_data()
        metrics = build_metrics(data)
        checks = run_checks(data, metrics)
        self.assertTrue(any(check.name == "Dashboard raspadita = detalle" for check in checks))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "filled.xlsx"
            write_workbook(str(path), data, checks)
            workbook = load_workbook(path, data_only=True)
            self.assertEqual(workbook.sheetnames, [
                "DASHBOARD", "PARTICIPANTES RASPADITA", "GANADORES", "PERDEDORES",
                "RETOS", "CUPONES"])
            dashboard = workbook["DASHBOARD"]
            metric_row = next(
                row[0].row for row in dashboard.iter_rows()
                if row[0].value == "Total de registros")
            self.assertEqual(dashboard.cell(metric_row, 2).value, metrics[0][0]["raspadita_total"])
            self.assertEqual(dashboard.cell(metric_row, 3).value, metrics[0][1]["raspadita_total"])
            self.assertEqual(dashboard.cell(metric_row, 4).value, metrics[1]["raspadita_total"])
            participants = workbook["PARTICIPANTES RASPADITA"]
            winners = workbook["GANADORES"]
            participants_headers = [
                participants.cell(4, col).value
                for col in range(1, participants.max_column + 1)
            ]
            winners_headers = [
                winners.cell(4, col).value
                for col in range(1, winners.max_column + 1)
            ]
            self.assertEqual(participants_headers, [
                "Fecha de registro", "ID del registro", "ID del cliente", "Nombre",
                "Apellido", "Correo", "Teléfono", "Variante", "ID de campaña", "Resultado",
            ])
            self.assertEqual(winners_headers, [
                "Fecha", "ID del cliente", "Nombre", "Apellido", "Correo", "Teléfono",
                "Variante", "Campaña",
            ])
            self.assertNotIn("Estado de apertura", participants_headers)
            self.assertNotIn("Cupón relacionado", winners_headers)
            self.assertNotIn("Estado del cupón", winners_headers)
            self.assertEqual(winners.row_dimensions[1].height, 24)
            self.assertIn("A1:H1", {str(rng) for rng in winners.merged_cells.ranges})
            self.assertLessEqual(winners.row_dimensions[4].height, 30)
            self.assertEqual(participants.max_row - 4, len(data.scratch))
            self.assertEqual(winners.max_row - 4, int((data.scratch["resultado"] == 1).sum()))
            self.assertEqual(participants.auto_filter.ref, "A4:J8")
            self.assertEqual(winners.auto_filter.ref, "A4:H6")
            self.assertEqual(participants.column_dimensions["A"].width, 22)
            self.assertEqual(winners.column_dimensions["A"].width, 22)
            self.assertEqual(participants["A5"].number_format, "dd/mm/yyyy hh:mm:ss")
            self.assertEqual(winners["A5"].number_format, "dd/mm/yyyy hh:mm:ss")
            self.assertIsNotNone(participants.freeze_panes)
            workbook.close()

    def test_query_errors_abort_instead_of_returning_empty_success(self):
        connection = _FakeConnection()

        @contextlib.contextmanager
        def fake_connection(_db):
            yield connection

        connection.cursor_instance.execute = mock.Mock(side_effect=RuntimeError("query failed"))
        with mock.patch("queries.dinamicas.get_connection", fake_connection):
            with self.assertLogs("queries.dinamicas", level="ERROR") as captured:
                with self.assertRaisesRegex(RuntimeError, "query failed"):
                    fetch_dynamics(None, _dynamics(), datetime.now(ZoneInfo("UTC")))
        self.assertIn("query failed", captured.output[0])

    def test_failed_checks_are_reported_to_console_and_logs(self):
        from export_dinamicas import _report_failed_checks

        output = io.StringIO()
        check = Check("Raspaditas: sin duplicados de registro", False, "2 duplicados")
        with self.assertLogs("export_dinamicas", level="WARNING") as captured:
            with contextlib.redirect_stderr(output):
                _report_failed_checks([check])
        self.assertIn("2 duplicados", output.getvalue())
        self.assertIn("2 duplicados", captured.output[0])

    def test_sql_failure_logs_technical_error(self):
        from queries.dinamicas import _run_source

        with self.assertLogs("queries.dinamicas", level="ERROR") as captured:
            with self.assertRaisesRegex(RuntimeError, "must appear in GROUP BY"):
                _run_source(
                    "historias",
                    lambda: (_ for _ in ()).throw(
                        RuntimeError("column must appear in GROUP BY clause")),
                )
        self.assertIn("must appear in GROUP BY", captured.output[0])

    def test_configured_missing_ids_make_source_not_available(self):
        dynamics = _dynamics(variants=1)
        connection = _FakeConnection(
            rows_by_source=_rows_by_source(),
            id_rows={"stories": [100], "campaigns": [],
                     "challenges": [300], "rewards": [400]})

        @contextlib.contextmanager
        def fake_connection(_db):
            yield connection

        with mock.patch("queries.dinamicas.get_connection", fake_connection):
            from queries.dinamicas import fetch_dynamics
            result = fetch_dynamics(None, dynamics, datetime.now(ZoneInfo("UTC")))
        self.assertIn("no encontrados", result.source_status["raspaditas"])
        self.assertIsNone(build_metrics(result)[1]["raspadita_total"])

    def test_sql_queries_are_read_only_and_period_parameters_are_bound(self):
        data, connection = self._fetch(desde=date(2026, 11, 1), hasta=date(2026, 11, 2))
        calls = connection.cursor_instance.calls
        for sql, _ in calls:
            self.assertTrue(
                sql.lstrip().upper().startswith("SELECT")
                or "information_schema.columns" in sql
                or "WITH configured" in sql
                or "WITH cumplimiento AS" in sql
            )
        scratch_sql, scratch_params = next(
            call for call in calls if "FROM campaigncustomer cc" in call[0])
        self.assertIn("%(desde)s", scratch_sql)
        self.assertIn("%(hasta)s", scratch_sql)
        self.assertEqual(scratch_params["desde"].date(), date(2026, 11, 1))
        self.assertEqual(scratch_params["hasta"].date(), date(2026, 11, 3))
        self.assertIn("GROUP BY challenge, customer", next(
            sql for sql, _ in calls if "WITH cumplimiento AS" in sql))
        self.assertEqual(data.source_status["raspaditas"], "Disponible")


@unittest.skipUnless(
    os.environ.get("PULSE_REPORTS_TEST_POSTGRES_DSN"),
    "Set PULSE_REPORTS_TEST_POSTGRES_DSN to an isolated PostgreSQL test database.",
)
class PostgreSQLStoryQueryRegressionTests(unittest.TestCase):
    def test_story_query_executes_with_multiple_variants_and_distinct_totals(self):
        import psycopg2

        dynamics = _dynamics()
        dynamics.variantes[0]["eventos_vista"] = ["view"]
        dynamics.variantes[0]["eventos_cta"] = ["tap"]
        connection = psycopg2.connect(os.environ["PULSE_REPORTS_TEST_POSTGRES_DSN"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TEMP TABLE storiescustomers (
                        id integer PRIMARY KEY, story integer, customer integer
                    ) ON COMMIT DROP;
                """)
                cursor.execute("""
                    CREATE TEMP TABLE story_actions (
                        story integer, customer integer, description text
                    ) ON COMMIT DROP;
                """)
                cursor.execute("""
                    INSERT INTO storiescustomers (id, story, customer) VALUES
                        (1, 100, 1), (2, 100, 2), (3, 101, 2);
                """)
                cursor.execute("""
                    INSERT INTO story_actions (story, customer, description) VALUES
                        (1, 1, 'view'), (2, 2, 'view'), (2, 2, 'tap'),
                        (3, 2, 'scroll');
                """)
            result = _query_stories(connection, dynamics).set_index("es_total")
            per_story = result[result.index == 0].set_index("historia_id")
            total = result.loc[1]
            self.assertEqual(int(per_story.loc[100, "total_vistas"]), 2)
            self.assertEqual(int(per_story.loc[100, "usuarios_unicos_vistas"]), 2)
            self.assertEqual(int(per_story.loc[100, "total_cta"]), 1)
            self.assertEqual(int(per_story.loc[101, "total_vistas"]), 1)
            self.assertEqual(int(total["total_vistas"]), 3)
            self.assertEqual(int(total["usuarios_unicos_vistas"]), 2)
            self.assertEqual(int(total["historias_asignadas"]), 3)
            self.assertEqual(int(total["usuarios_asignados"]), 2)
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
