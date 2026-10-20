"""
reports/charts.py
Genera gráficos Matplotlib y los devuelve como BytesIO para incrustar en PDF.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import matplotlib
matplotlib.use("Agg")   # sin pantalla
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

logger = logging.getLogger(__name__)

# ── Paleta corporativa ────────────────────────────────────────────────────────
BLUE      = "#1A3C6E"
BLUE_LIGHT= "#2E6DAD"
GREEN     = "#2ECC71"
RED       = "#E74C3C"
ORANGE    = "#E67E22"
GRAY      = "#95A5A6"
BG        = "#F8FAFC"

PALETTE   = [BLUE, BLUE_LIGHT, GREEN, ORANGE, RED, GRAY,
             "#8E44AD", "#16A085", "#D35400", "#2C3E50"]


def _save(fig: plt.Figure) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    buf.seek(0)
    plt.close(fig)
    return buf


def _base_fig(w: float = 9, h: float = 4) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors="#444", labelsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"{int(x):,}"
    ))
    return fig, ax


# ── Gráficos de Trivias ───────────────────────────────────────────────────────

def chart_rendimiento_trivias(df: pd.DataFrame,
                               pais: str) -> Optional[io.BytesIO]:
    """Barras apiladas: correctas vs incorrectas por trivia."""
    if df.empty:
        return None
    try:
        fig, ax = _base_fig(10, max(4, len(df) * 0.45))
        labels = df["trivia"].str[:30].tolist()
        correctas   = df["correctas"].fillna(0).astype(int).tolist()
        incorrectas = df["incorrectas"].fillna(0).astype(int).tolist()
        y = range(len(labels))

        ax.barh(y, correctas,   color=GREEN,  label="Correctas",   height=0.55)
        ax.barh(y, incorrectas, left=correctas, color=RED, label="Incorrectas", height=0.55)
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlabel("Respuestas", fontsize=8)
        ax.set_title(f"Rendimiento Trivias — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE, pad=10)
        ax.legend(fontsize=8, loc="lower right")
        ax.invert_yaxis()
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_rendimiento_trivias: %s", exc)
        return None


def chart_participacion_trivias(df: pd.DataFrame,
                                 pais: str) -> Optional[io.BytesIO]:
    """Barras horizontales: veces jugado por trivia."""
    if df.empty or "veces_jugado" not in df.columns:
        return None
    try:
        top = df.nlargest(15, "veces_jugado")
        fig, ax = _base_fig(9, max(4, len(top) * 0.45))
        labels = top["trivia"].str[:30].tolist()
        vals   = top["veces_jugado"].fillna(0).astype(int).tolist()
        bars = ax.barh(range(len(labels)), vals, color=BLUE_LIGHT, height=0.6)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_title(f"Participación Trivias (Top 15) — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE, pad=10)
        ax.set_xlabel("Veces jugado", fontsize=8)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_width() + max(vals) * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{val:,}", va="center", fontsize=7, color="#333")
        ax.invert_yaxis()
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_participacion_trivias: %s", exc)
        return None


# ── Gráficos de Premios ───────────────────────────────────────────────────────

def chart_premios_hoy(df: pd.DataFrame, pais: str) -> Optional[io.BytesIO]:
    """Barras verticales de premios entregados hoy."""
    if df.empty:
        return None
    try:
        fig, ax = _base_fig(8, 4)
        labels = df["premio"].str[:25].tolist()
        vals   = df["entregados_hoy"].fillna(0).astype(int).tolist()
        colors = [PALETTE[i % len(PALETTE)] for i in range(len(labels))]
        bars   = ax.bar(range(len(labels)), vals, color=colors, width=0.6)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
        ax.set_title(f"Premios Entregados Hoy — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE, pad=10)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(vals) * 0.02,
                    f"{val:,}", ha="center", fontsize=8, color="#333")
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_premios_hoy: %s", exc)
        return None


def chart_historico_premios(df: pd.DataFrame, pais: str) -> Optional[io.BytesIO]:
    """Barras horizontales: total entregado + promedio diario."""
    if df.empty:
        return None
    try:
        fig, ax = _base_fig(9, max(4, len(df) * 0.5))
        labels = df["premio"].str[:30].tolist()
        totales = df["entregados_total"].fillna(0).astype(int).tolist()
        promedios = df["promedio_x_dia"].fillna(0).astype(float).tolist()
        y = range(len(labels))
        ax.barh(y, totales, color=BLUE, height=0.5, label="Total entregado")
        ax2 = ax.twiny()
        ax2.barh([i + 0.3 for i in y], promedios, color=ORANGE,
                 height=0.35, label="Promedio/día")
        ax2.set_xlabel("Promedio diario", fontsize=8, color=ORANGE)
        ax2.tick_params(colors=ORANGE, labelsize=7)
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlabel("Total entregado", fontsize=8, color=BLUE)
        ax.set_title(f"Histórico de Premios — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE, pad=10)
        ax.invert_yaxis()
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="lower right")
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_historico_premios: %s", exc)
        return None


def chart_estado_reclamos(df: pd.DataFrame, pais: str) -> Optional[io.BytesIO]:
    """Pastel de distribución de estados de premios reclamados."""
    if df.empty or "estado_premio" not in df.columns:
        return None
    try:
        conteo = df["estado_premio"].value_counts()
        if conteo.empty:
            return None
        fig, ax = plt.subplots(figsize=(6, 5))
        fig.patch.set_facecolor(BG)
        wedges, texts, autotexts = ax.pie(
            conteo.values,
            labels=None,
            autopct="%1.1f%%",
            colors=PALETTE[:len(conteo)],
            pctdistance=0.8,
            startangle=90,
        )
        for t in autotexts:
            t.set_fontsize(8)
        ax.legend(
            wedges, [f"{lbl} ({cnt:,})" for lbl, cnt in zip(conteo.index, conteo.values)],
            loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8
        )
        ax.set_title(f"Estado de Premios Reclamados — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE)
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_estado_reclamos: %s", exc)
        return None


def chart_stock_gp(df: pd.DataFrame, pais: str) -> Optional[io.BytesIO]:
    """Barras agrupadas: stock total vs ganados por premio ganador."""
    if df.empty:
        return None
    ganadores = df[df["tipo"] == "ganador"].copy()
    if ganadores.empty:
        return None
    try:
        ganadores = ganadores.sort_values("stock_total", ascending=False).head(12)
        labels  = ganadores["premio"].str[:25].tolist()
        stock   = ganadores["stock_total"].fillna(0).astype(int).tolist()
        ganados = ganadores["ganados"].fillna(0).astype(int).tolist()
        x       = list(range(len(labels)))
        fig, ax = _base_fig(10, 5)
        w = 0.38
        ax.bar([i - w/2 for i in x], stock,   width=w, color=BLUE,  label="Stock total")
        ax.bar([i + w/2 for i in x], ganados, width=w, color=GREEN, label="Ganados")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
        ax.set_title(f"Stock GP vs Ganados — {pais}", fontsize=10,
                     fontweight="bold", color=BLUE, pad=10)
        ax.legend(fontsize=8)
        fig.tight_layout()
        return _save(fig)
    except Exception as exc:
        logger.error("chart_stock_gp: %s", exc)
        return None
