"""Indicadores y controles de integridad del reporte de dinamicas."""
from dataclasses import dataclass
import pandas as pd

from queries.dinamicas import DynamicsData


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def _available(data: DynamicsData, source: str) -> bool:
    return data.source_status.get(source, "").startswith("Disponible")


def _number(value) -> int:
    return 0 if pd.isna(value) else int(value)


def _unique(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].dropna().nunique()) if column in frame else 0


def _completion_records(frame: pd.DataFrame) -> int | None:
    if not {"reto_id", "cliente_id", "cantidad_registros_cumplimiento"}.issubset(frame.columns):
        return None
    unique_pairs = frame.drop_duplicates(["reto_id", "cliente_id"])
    return _number(pd.to_numeric(
        unique_pairs["cantidad_registros_cumplimiento"], errors="coerce").sum())


def _percent(numerator, denominator):
    if numerator is None or denominator is None or not denominator:
        return None
    return numerator / denominator


def _variant_frame(frame: pd.DataFrame, column: str, value: int) -> pd.DataFrame:
    if column not in frame:
        return frame.iloc[0:0]
    return frame[frame[column] == value]


def build_metrics(data: DynamicsData) -> tuple[list[dict], dict]:
    """Calcula métricas por variante y total real; None significa N/D."""
    variant_rows = []
    story = data.story_summary
    story_total = (
        story[story["es_total"] == 1].iloc[0]
        if not story.empty and "es_total" in story and (story["es_total"] == 1).any()
        else None
    )
    story_by_id = (
        story[story["es_total"] == 0].set_index("historia_id")
        if not story.empty and "es_total" in story else pd.DataFrame()
    )

    for variant in data.dinamica.variantes:
        sid = variant["historia_id"]
        row = story_by_id.loc[sid] if sid in story_by_id.index else None
        story_ok = _available(data, "historias")
        known_events = data.story_action_descriptions.get(sid, set())
        view_events = variant.get("eventos_vista")
        cta_events = variant.get("eventos_cta")
        view_known = story_ok and (
            view_events is None or set(view_events).issubset(known_events))
        cta_known = story_ok and bool(cta_events) and set(cta_events).issubset(known_events)
        views = _number(row["total_vistas"]) if view_known and row is not None else None
        viewers = (
            _number(row["usuarios_unicos_vistas"])
            if view_known and row is not None else None
        )
        assigned = _number(row["historias_asignadas"]) if story_ok and row is not None else None
        assigned_users = _number(row["usuarios_asignados"]) if story_ok and row is not None else None
        cta_count = _number(row["total_cta"]) if cta_known and row is not None else None
        cta_users = _number(row["usuarios_cta"]) if cta_known and row is not None else None

        scratch = _variant_frame(
            data.scratch, "campania_id", variant["campania_raspadita_id"])
        scratch_ok = _available(data, "raspaditas")
        result = scratch["resultado"] if scratch_ok and "resultado" in scratch else pd.Series(dtype=object)
        scratch_total = len(scratch) if scratch_ok else None
        scratch_wins = int((result == 1).sum()) if scratch_ok else None
        scratch_losses = int((result == 0).sum()) if scratch_ok else None
        scratch_unclassified = int(result.isna().sum()) if scratch_ok else None
        scratch_classified = (
            scratch_wins + scratch_losses if scratch_ok else None
        )

        challenge = _variant_frame(data.challenges, "reto_id", variant["reto_id"])
        challenge_ok = _available(data, "retos")
        accepted_users = _unique(challenge, "cliente_id") if challenge_ok else None
        completed_users = (
            _unique(challenge[challenge["completado"]], "cliente_id")
            if challenge_ok and "completado" in challenge else None
        )
        pending_users = (
            max(accepted_users - completed_users, 0)
            if accepted_users is not None and completed_users is not None else None
        )
        completion_records = (
            _completion_records(challenge) if challenge_ok else None
        )

        coupon = _variant_frame(data.coupons, "cupon_id", variant["cupon_id"])
        coupon_ok = _available(data, "cupones")
        status = coupon["estado"] if coupon_ok and "estado" in coupon else pd.Series(dtype=object)
        coupon_total = len(coupon) if coupon_ok else None
        redeemed = int((status == 0).sum()) if coupon_ok else None
        not_redeemed = int((status == 1).sum()) if coupon_ok else None
        expired = int((status == 2).sum()) if coupon_ok else None
        cancelled = int((status == -1).sum()) if coupon_ok else None

        variant_rows.append({
            "codigo": variant["codigo"],
            "nombre": variant["nombre"],
            "historia_id": sid,
            "raspadita_total": scratch_total,
            "raspadita_usuarios": _unique(scratch, "cliente_id") if scratch_ok else None,
            "raspadita_ganadores": scratch_wins,
            "raspadita_perdedores": scratch_losses,
            "raspadita_sin_resultado": scratch_unclassified,
            "raspadita_usuarios_ganadores": (
                _unique(scratch[result == 1], "cliente_id") if scratch_ok else None),
            "raspadita_usuarios_perdedores": (
                _unique(scratch[result == 0], "cliente_id") if scratch_ok else None),
            "raspadita_pct_ganadores": _percent(scratch_wins, scratch_classified),
            "reto_aceptaciones": len(challenge) if challenge_ok else None,
            "reto_usuarios_aceptaron": accepted_users,
            "reto_usuarios_completaron": completed_users,
            "reto_usuarios_pendientes": pending_users,
            "reto_registros_cumplimiento": completion_records,
            "reto_pct_cumplimiento": _percent(completed_users, accepted_users),
            "cupones_asignados": coupon_total,
            "cupones_clientes": _unique(coupon, "cliente_id") if coupon_ok else None,
            "cupones_canjeados": redeemed,
            "cupones_sin_canjear": not_redeemed,
            "cupones_vencidos": expired,
            "cupones_cancelados": cancelled,
            "cupones_pct_canje": _percent(redeemed, coupon_total),
            "historias_asignadas": assigned,
            "historias_usuarios_asignados": assigned_users,
            "historias_vistas": views,
            "historias_usuarios_vistas": viewers,
            "historias_total_cta": cta_count,
            "historias_usuarios_cta": cta_users,
            "historias_pct_cta": _percent(cta_users, viewers),
        })

    scratch_ok = _available(data, "raspaditas")
    challenge_ok = _available(data, "retos")
    coupon_ok = _available(data, "cupones")
    story_ok = _available(data, "historias")
    all_views_known = story_ok and all(
        v.get("eventos_vista") is None
        or set(v["eventos_vista"]).issubset(
            data.story_action_descriptions.get(v["historia_id"], set()))
        for v in data.dinamica.variantes)
    all_cta_known = story_ok and all(
        v.get("eventos_cta")
        and set(v["eventos_cta"]).issubset(
            data.story_action_descriptions.get(v["historia_id"], set()))
        for v in data.dinamica.variantes)
    scratch_result = data.scratch["resultado"] if scratch_ok and "resultado" in data.scratch else pd.Series(dtype=object)
    coupon_status = data.coupons["estado"] if coupon_ok and "estado" in data.coupons else pd.Series(dtype=object)

    total = {
        "codigo": "TOTAL",
        "nombre": "TOTAL GENERAL",
        "raspadita_total": int(sum(r["raspadita_total"] or 0 for r in variant_rows)) if scratch_ok else None,
        "raspadita_usuarios": _unique(data.scratch, "cliente_id") if scratch_ok else None,
        "raspadita_ganadores": int((scratch_result == 1).sum()) if scratch_ok else None,
        "raspadita_perdedores": int((scratch_result == 0).sum()) if scratch_ok else None,
        "raspadita_sin_resultado": int(scratch_result.isna().sum()) if scratch_ok else None,
        "raspadita_usuarios_ganadores": (
            _unique(data.scratch[scratch_result == 1], "cliente_id") if scratch_ok else None),
        "raspadita_usuarios_perdedores": (
            _unique(data.scratch[scratch_result == 0], "cliente_id") if scratch_ok else None),
        "reto_aceptaciones": len(data.challenges) if challenge_ok else None,
        "reto_usuarios_aceptaron": _unique(data.challenges, "cliente_id") if challenge_ok else None,
        "reto_usuarios_completaron": (
            _unique(data.challenges[data.challenges["completado"]], "cliente_id")
            if challenge_ok and "completado" in data.challenges else None),
        "reto_registros_cumplimiento": (
            _completion_records(data.challenges) if challenge_ok else None),
        "cupones_asignados": len(data.coupons) if coupon_ok else None,
        "cupones_clientes": _unique(data.coupons, "cliente_id") if coupon_ok else None,
        "cupones_canjeados": int((coupon_status == 0).sum()) if coupon_ok else None,
        "cupones_sin_canjear": int((coupon_status == 1).sum()) if coupon_ok else None,
        "cupones_vencidos": int((coupon_status == 2).sum()) if coupon_ok else None,
        "cupones_cancelados": int((coupon_status == -1).sum()) if coupon_ok else None,
        "historias_asignadas": (
            _number(story_total["historias_asignadas"])
            if story_ok and story_total is not None else None),
        "historias_usuarios_asignados": (
            _number(story_total["usuarios_asignados"])
            if story_ok and story_total is not None else None),
        "historias_vistas": (
            _number(story_total["total_vistas"])
            if all_views_known and story_total is not None else None),
        "historias_usuarios_vistas": (
            _number(story_total["usuarios_unicos_vistas"])
            if all_views_known and story_total is not None else None),
        "historias_total_cta": (
            _number(story_total["total_cta"])
            if all_cta_known and story_total is not None else None),
        "historias_usuarios_cta": (
            _number(story_total["usuarios_cta"])
            if all_cta_known and story_total is not None else None),
    }
    total["raspadita_pct_ganadores"] = _percent(
        total["raspadita_ganadores"],
        (total["raspadita_ganadores"] or 0) + (total["raspadita_perdedores"] or 0)
        if scratch_ok else None)
    total["reto_usuarios_pendientes"] = (
        max(total["reto_usuarios_aceptaron"] - total["reto_usuarios_completaron"], 0)
        if total["reto_usuarios_aceptaron"] is not None
        and total["reto_usuarios_completaron"] is not None else None)
    total["reto_pct_cumplimiento"] = _percent(
        total["reto_usuarios_completaron"], total["reto_usuarios_aceptaron"])
    total["cupones_pct_canje"] = _percent(
        total["cupones_canjeados"], total["cupones_asignados"])
    total["historias_pct_cta"] = _percent(
        total["historias_usuarios_cta"], total["historias_usuarios_vistas"])
    return variant_rows, total


def run_checks(data: DynamicsData, metrics: tuple[list[dict], dict]) -> list[Check]:
    variants, total = metrics
    story = data.story_summary
    checks = [
        Check("Corte del reporte registrado", data.generated_at is not None,
              data.generated_at.isoformat() if data.generated_at else "Sin fecha de corte"),
    ]
    for source, status in data.source_status.items():
        checks.append(Check(
            f"Fuente {source}", status.startswith("Disponible"), status,
            blocking=False))

    if _available(data, "raspaditas"):
        detail = data.scratch
        unexpected = detail[
            detail["resultado"].notna()
            & ~detail["resultado"].isin([0, 1])
        ] if "resultado" in detail else detail
        categories = (
            len(detail) == int((detail["resultado"] == 1).sum())
            + int((detail["resultado"] == 0).sum())
            + int(detail["resultado"].isna().sum())
        ) if "resultado" in detail else False
        checks.extend([
            Check("Raspaditas: total = categorías", categories,
                  f"{len(detail)} registros; ganadores/perdedores/sin resultado/"
                  f"inesperados={int((detail['resultado'] == 1).sum())}/"
                  f"{int((detail['resultado'] == 0).sum())}/"
                  f"{int(detail['resultado'].isna().sum())}/{len(unexpected)}"),
            Check("Raspaditas: valores de resultado esperados", unexpected.empty,
                  f"{len(unexpected)} registros fuera de 0, 1 o NULL"),
            Check("Raspaditas: sin duplicados de registro",
                  "registro_id" in detail and detail["registro_id"].is_unique,
                  f"{detail['registro_id'].duplicated().sum() if 'registro_id' in detail else 'N/D'} duplicados"),
            Check("Raspaditas: campañas corresponden a configuración",
                  detail.empty or detail["campania_id"].isin(
                      [v["campania_raspadita_id"] for v in data.dinamica.variantes]).all(),
                  "Todas las campañas pertenecen a la configuración"),
            Check("Raspaditas: registros sin cliente",
                  "cliente_id" in detail and not detail["cliente_id"].isna().any(),
                  f"{int(detail['cliente_id'].isna().sum()) if 'cliente_id' in detail else 'N/D'}"),
        ])
        expected = sum(row["raspadita_total"] or 0 for row in variants)
        checks.append(Check("Dashboard raspadita = detalle", total["raspadita_total"] == len(detail)
                            and expected == len(detail),
                            f"Dashboard={total['raspadita_total']}, detalle={len(detail)}, variantes={expected}"))
        checks.append(Check(
            "Detalle de ganadores/perdedores = dashboard",
            total["raspadita_ganadores"] == int((detail["resultado"] == 1).sum())
            and total["raspadita_perdedores"] == int((detail["resultado"] == 0).sum()),
            f"Ganadores={total['raspadita_ganadores']}/{int((detail['resultado'] == 1).sum())}; "
            f"perdedores={total['raspadita_perdedores']}/{int((detail['resultado'] == 0).sum())}"))

    if _available(data, "retos"):
        detail = data.challenges
        duplicate = "aceptacion_id" not in detail or not detail["aceptacion_id"].is_unique
        checks.append(Check("Retos: sin duplicados de aceptación", not duplicate,
                            f"{detail['aceptacion_id'].duplicated().sum() if 'aceptacion_id' in detail else 'N/D'} duplicados"))
        unexpected = detail[
            ~detail["reto_id"].isin([v["reto_id"] for v in data.dinamica.variantes])
        ] if "reto_id" in detail else detail
        checks.append(Check("Retos: IDs corresponden a configuración", unexpected.empty,
                            f"{len(unexpected)} aceptaciones de retos no configurados"))
        checks.append(Check("Dashboard retos = detalle",
                            total["reto_aceptaciones"] == len(detail),
                            f"Dashboard={total['reto_aceptaciones']}, detalle={len(detail)}"))
        if total["reto_usuarios_aceptaron"] is not None:
            checks.append(Check(
                "Retos: aceptados = completados + pendientes",
                total["reto_usuarios_aceptaron"]
                == total["reto_usuarios_completaron"] + total["reto_usuarios_pendientes"],
                f"Aceptados={total['reto_usuarios_aceptaron']}; completados="
                f"{total['reto_usuarios_completaron']}; pendientes="
                f"{total['reto_usuarios_pendientes']}"))
        checks.append(Check(
            "Retos: aceptaciones sin cliente",
            "cliente_id" in detail and not detail["cliente_id"].isna().any(),
            f"{int(detail['cliente_id'].isna().sum()) if 'cliente_id' in detail else 'N/D'}"))

    if _available(data, "cupones"):
        detail = data.coupons
        invalid = detail[
            detail["estado"].isna() | ~detail["estado"].isin([0, 1, 2, -1])
        ] if "estado" in detail else detail
        duplicate = "asignacion_id" not in detail or not detail["asignacion_id"].is_unique
        checks.extend([
            Check("Cupones: sin duplicados de asignación", not duplicate,
                  f"{detail['asignacion_id'].duplicated().sum() if 'asignacion_id' in detail else 'N/D'} duplicados"),
            Check("Cupones: estados conocidos", invalid.empty,
                  f"{len(invalid)} estados nulos o fuera de 0, 1, 2 y -1"),
            Check("Cupones: asignaciones sin cliente",
                  "cliente_id" in detail and not detail["cliente_id"].isna().any(),
                  f"{int(detail['cliente_id'].isna().sum()) if 'cliente_id' in detail else 'N/D'}"),
            Check("Cupones: IDs corresponden a configuración",
                  detail.empty or detail["cupon_id"].isin(
                      [v["cupon_id"] for v in data.dinamica.variantes]).all(),
                  "Todos los cupones pertenecen a la configuración"),
            Check("Dashboard cupones = detalle",
                  total["cupones_asignados"] == len(detail),
                  f"Dashboard={total['cupones_asignados']}, detalle={len(detail)}"),
        ])
        status_counts = sum(int((detail["estado"] == value).sum()) for value in (0, 1, 2, -1))
        checks.append(Check(
            "Cupones: total = estados clasificados",
            status_counts == len(detail),
            f"Asignados={len(detail)}; estados clasificados={status_counts}; "
            f"no clasificados={len(detail) - status_counts}"))

    if _available(data, "historias"):
        expected_ids = {variant["historia_id"] for variant in data.dinamica.variantes}
        actual_ids = (
            set(story.loc[story["es_total"] == 0, "historia_id"].dropna())
            if {"es_total", "historia_id"}.issubset(story.columns) else set()
        )
        total_rows = (
            int((story["es_total"] == 1).sum()) if "es_total" in story else 0
        )
        checks.append(Check(
            "Historias: resumen cubre variantes y total",
            actual_ids == expected_ids and total_rows == 1,
            f"Variantes={len(actual_ids)}/{len(expected_ids)}; filas de total={total_rows}"))
        unclassified = []
        for variant in data.dinamica.variantes:
            if (
                not variant.get("eventos_cta")
                or not set(variant["eventos_cta"]).issubset(
                    data.story_action_descriptions.get(variant["historia_id"], set()))
                or (variant.get("eventos_vista") is not None
                    and not set(variant["eventos_vista"]).issubset(
                        data.story_action_descriptions.get(
                            variant["historia_id"], set())))
            ):
                unclassified.append(variant["codigo"])
        checks.append(Check(
            "Eventos de historias clasificados", not unclassified,
            ("Vista/CTA N/D hasta confirmar descripciones en story_actions.description: "
             + ", ".join(unclassified))
            if unclassified else "Descripciones observadas en story_actions.description",
            blocking=False))
    return checks


def information_state(data: DynamicsData, checks: list[Check] | None = None) -> str:
    if checks and any(not check.ok and check.blocking for check in checks):
        return "INCOMPLETA: hay validaciones fallidas"
    required = ("historias", "raspaditas", "retos", "cupones")
    if all(_available(data, source) for source in required):
        _, total = build_metrics(data)
        if (
            total["historias_vistas"] is None
            or total["historias_usuarios_cta"] is None
            or any("no verificable" in data.source_status[source].lower()
                   for source in required)
        ):
            return "PARCIAL: eventos de historia sin clasificación validada"
        return "COMPLETA"
    return "INCOMPLETA: una o más fuentes no están disponibles"
