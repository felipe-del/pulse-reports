"""
reports/alerts.py
Motor de detección de alertas sobre los DataFrames ya cargados.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from config.settings import AlertConfig

logger = logging.getLogger(__name__)

AlertLevel = Literal["critica", "advertencia", "info"]


@dataclass
class Alert:
    level: AlertLevel
    icon: str
    title: str
    detail: str

    def __str__(self) -> str:
        return f"{self.icon} [{self.level.upper()}] {self.title}: {self.detail}"


@dataclass
class AlertReport:
    alerts: list[Alert] = field(default_factory=list)

    def add(self, level: AlertLevel, icon: str, title: str, detail: str) -> None:
        a = Alert(level=level, icon=icon, title=title, detail=detail)
        self.alerts.append(a)
        logger.info("Alerta generada: %s", a)

    @property
    def has_critical(self) -> bool:
        return any(a.level == "critica" for a in self.alerts)

    def by_level(self, level: AlertLevel) -> list[Alert]:
        return [a for a in self.alerts if a.level == level]

    def summary_text(self) -> str:
        if not self.alerts:
            return "✅ *SIN ALERTAS ACTIVAS*"

        lines = ["🚨 *RESUMEN DE ALERTAS*", ""]

        for lvl, icon, title in [
            ("critica", "🔴", "CRÍTICAS"),
            ("advertencia", "🟡", "ADVERTENCIAS"),
            ("info", "🔵", "INFORMATIVAS"),
        ]:
            group = self.by_level(lvl)  # type: ignore[arg-type]
            if not group:
                continue

            lines.append(f"{icon} *{title}* ({len(group)})")
            lines.append("")

            for a in group:
                lines.append(f"📌 *{a.title}*")
                lines.append(f"   └ {a.detail}")
                lines.append("")

        return "\n".join(lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Verificadores individuales
# ─────────────────────────────────────────────────────────────────────────────

def check_stock_gp(df_gp: pd.DataFrame, cfg: AlertConfig, pais: str,
                   report: AlertReport) -> None:
    """Alerta cuando el stock restante de un premio ganador baja del umbral."""
    if df_gp.empty:
        return
    ganadores = df_gp[df_gp["tipo"] == "ganador"].copy()
    ganadores = ganadores[ganadores["stock_total"] > 0]
    ganadores["pct_restante"] = ganadores["faltan_por_entregar"] / ganadores["stock_total"]

    agotados = ganadores[ganadores["faltan_por_entregar"] <= 0]
    for _, row in agotados.iterrows():
        report.add(
            "critica", "🔴",
            f"[{pais}] Premio agotado",
            f"'{row['premio']}' — campaña {row['campaign_id']}",
        )

    proximos = ganadores[
        (ganadores["faltan_por_entregar"] > 0) &
        (ganadores["pct_restante"] <= cfg.stock_threshold)
    ]
    for _, row in proximos.iterrows():
        pct = round(row["pct_restante"] * 100, 1)
        report.add(
            "advertencia", "🟡",
            f"[{pais}] Stock crítico",
            f"'{row['premio']}' — {row['faltan_por_entregar']} unidades ({pct}% restante)",
        )


def check_errores_entrega(df_reclamos: pd.DataFrame, pais: str,
                          report: AlertReport) -> None:
    """Alerta sobre premios con error en entrega automática."""
    if df_reclamos.empty:
        return
    errores = df_reclamos[df_reclamos["estado_premio"] == "Error en entrega"]
    if not errores.empty:
        report.add(
            "critica", "🔴",
            f"[{pais}] Errores en entrega automática",
            f"{len(errores)} premio(s) con error — revisar fulfillment",
        )


def check_trivias_caida(df_actual: pd.DataFrame, df_anterior: pd.DataFrame,
                        pais: str, cfg: AlertConfig,
                        report: AlertReport) -> None:
    """
    Detecta trivias con caída significativa de participación.
    Requiere dos snapshots (actual vs anterior) para comparar.
    """
    if df_actual.empty or df_anterior.empty:
        return
    merged = df_actual[["id", "veces_jugado"]].merge(
        df_anterior[["id", "veces_jugado"]].rename(columns={"veces_jugado": "ant"}),
        on="id", how="inner"
    )
    merged = merged[merged["ant"] > 0]
    merged["delta"] = (merged["veces_jugado"] - merged["ant"]) / merged["ant"]
    caidas = merged[merged["delta"] <= -cfg.trivia_drop_threshold]
    for _, row in caidas.iterrows():
        pct = round(abs(row["delta"]) * 100, 1)
        report.add(
            "advertencia", "🟡",
            f"[{pais}] Caída de participación en trivia",
            f"ID {int(row['id'])} — caída del {pct}%",
        )


def check_sin_entregas(df_hoy: pd.DataFrame, all_reward_ids: list[int],
                       pais: str, report: AlertReport) -> None:
    """Alerta si algún premio no registró entregas en el día actual."""
    if df_hoy.empty:
        report.add(
            "advertencia", "🟡",
            f"[{pais}] Sin entregas de premios hoy",
            "No se registraron entregas en el día.",
        )
        return
    ids_con_entrega = set(df_hoy["reward_id"].tolist())
    sin_entrega = [i for i in all_reward_ids if i not in ids_con_entrega]
    if sin_entrega:
        report.add(
            "info", "🔵",
            f"[{pais}] Premios sin entregas hoy",
            f"IDs: {sin_entrega}",
        )
