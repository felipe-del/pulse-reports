"""
reports/pdf_generator.py
Genera el PDF completo del reporte Pulse Reports usando ReportLab.

Regla de paginación:
  • Cada sección/país abre página nueva.
  • Título + gráfico son inseparables (KeepTogether) — el título NUNCA
    queda solo al final de una página sin su gráfico debajo.
  • La tabla de datos sigue inmediatamente debajo del gráfico en la
    misma página; si es larga ReportLab la continúa en la siguiente.
"""
from __future__ import annotations

import io
import logging
import os
from datetime import datetime
from typing import Optional

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

from reports.alerts import AlertReport

logger = logging.getLogger(__name__)

# ── Colores corporativos ──────────────────────────────────────────────────────
C_BLUE       = colors.HexColor("#1A3C6E")
C_BLUE_LIGHT = colors.HexColor("#2E6DAD")
C_GREEN      = colors.HexColor("#2ECC71")
C_RED        = colors.HexColor("#E74C3C")
C_ORANGE     = colors.HexColor("#E67E22")
C_GRAY_LIGHT = colors.HexColor("#EEF2F7")
C_GRAY_MID   = colors.HexColor("#BDC3C7")
C_WHITE      = colors.white
C_DARK       = colors.HexColor("#2C3E50")

PAGE_W, PAGE_H = A4
CONTENT_W = PAGE_W - 2.4 * cm
CONTENT_H = PAGE_H - 3.2 * cm   # header 1.8 + footer 0.8 + margen 0.6


# ─────────────────────────────────────────────────────────────────────────────
# Estilos
# ─────────────────────────────────────────────────────────────────────────────

def _styles() -> dict:
    return {
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20,
                              textColor=C_BLUE, spaceAfter=4),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13,
                              textColor=C_BLUE, spaceAfter=2, spaceBefore=2),
        "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10,
                              textColor=C_BLUE_LIGHT, spaceAfter=2, spaceBefore=2),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=8,
                               textColor=C_DARK, leading=12),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7,
                                textColor=colors.gray, leading=10),
        "alert_crit": ParagraphStyle("alert_crit", fontName="Helvetica-Bold",
                                     fontSize=8, textColor=C_RED),
        "alert_warn": ParagraphStyle("alert_warn", fontName="Helvetica-Bold",
                                     fontSize=8, textColor=C_ORANGE),
        "alert_info": ParagraphStyle("alert_info", fontName="Helvetica",
                                     fontSize=8, textColor=C_BLUE_LIGHT),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de layout
# ─────────────────────────────────────────────────────────────────────────────

def _df_to_table(df: pd.DataFrame,
                 col_widths: list[float] | None = None,
                 max_rows: int = 200) -> Table:
    truncated = df.head(max_rows)
    header = [str(c) for c in truncated.columns]
    data   = [header] + [
        [str(v) if v is not None else "" for v in row]
        for row in truncated.itertuples(index=False)
    ]
    available_w = CONTENT_W
    if col_widths is None:
        n = len(header)
        col_widths = [available_w / n] * n
    else:
        total = sum(col_widths)
        if total <= 1.01:
            col_widths = [p * available_w for p in col_widths]

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING",    (0, 0), (-1, 0), 5),
        ("FONTSIZE",      (0, 1), (-1, -1), 7),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_GRAY_LIGHT]),
        ("GRID",          (0, 0), (-1, -1), 0.25, C_GRAY_MID),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]))
    return t


def _page_title(section: str, pais: str, styles: dict) -> list:
    """Encabezado de página: sección + país + línea divisoria."""
    return [
        Paragraph(section, styles["h2"]),
        Paragraph(pais, styles["h3"]),
        HRFlowable(width="100%", thickness=1.5, color=C_BLUE_LIGHT,
                   spaceAfter=3 * mm),
    ]


def _image_from_buf(buf: Optional[io.BytesIO],
                    max_w: float = CONTENT_W,
                    max_h: float = 10 * cm) -> Optional[Image]:
    if buf is None:
        return None
    try:
        img = Image(buf)
        ratio = img.imageWidth / img.imageHeight
        w, h = float(img.imageWidth), float(img.imageHeight)
        if w > max_w:
            w = max_w
            h = w / ratio
        if h > max_h:
            h = max_h
            w = h * ratio
        img.drawWidth  = w
        img.drawHeight = h
        return img
    except Exception as exc:
        logger.warning("No se pudo cargar imagen: %s", exc)
        return None


def _chart_page(section: str, pais: str, chart_buf,
                table: Optional[Table], styles: dict,
                note: str = "") -> list:
    """
    Flowables para UNA página:
      PageBreak → KeepTogether(título + gráfico) → nota? → tabla?

    - PageBreak:    fuerza página nueva antes del bloque.
    - KeepTogether: une título y gráfico de forma inseparable; el título
                    NUNCA queda solo al final de una página.
    - La tabla sigue debajo en la misma página; si es larga ReportLab
      la continúa en la siguiente con repeatRows.
    """
    # Bloque inseparable: título de sección + gráfico
    header_and_chart: list = _page_title(section, pais, styles)
    img = _image_from_buf(chart_buf)
    if img:
        header_and_chart.append(img)
        header_and_chart.append(Spacer(1, 3 * mm))

    items: list = [
        PageBreak(),
        KeepTogether(header_and_chart),
    ]

    if note:
        items.append(Paragraph(note, styles["small"]))
        items.append(Spacer(1, 2 * mm))

    if table is not None:
        items.append(table)

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Encabezado y pie de página
# ─────────────────────────────────────────────────────────────────────────────

def _make_page_templates(doc: BaseDocTemplate) -> list[PageTemplate]:
    def _header_footer(canvas, doc_):
        canvas.saveState()
        canvas.setFillColor(C_BLUE)
        canvas.rect(0, PAGE_H - 1.8 * cm, PAGE_W, 1.8 * cm, fill=1, stroke=0)
        canvas.setFillColor(C_WHITE)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(1.2 * cm, PAGE_H - 1.2 * cm,
                          "Pulse Reports  ·  Reporte Automático")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_W - 1.2 * cm, PAGE_H - 1.2 * cm,
                               datetime.now().strftime("%d/%m/%Y  %H:%M"))
        canvas.setFillColor(C_GRAY_MID)
        canvas.rect(0, 0, PAGE_W, 0.8 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.gray)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(PAGE_W / 2, 0.28 * cm, f"Página {doc_.page}")
        canvas.restoreState()

    frame = Frame(1.2 * cm, 1 * cm, CONTENT_W, CONTENT_H, id="normal")
    return [PageTemplate(id="main", frames=[frame], onPage=_header_footer)]


# ─────────────────────────────────────────────────────────────────────────────
# Constructor principal
# ─────────────────────────────────────────────────────────────────────────────

class GPReportPDF:
    """
    Construye el PDF completo del reporte Pulse Reports.

    Estructura de páginas:
      Pág 1  — Portada
      Pág 2  — Resumen Ejecutivo (KPIs) + Alertas
      Pág 3+ — Una página por cada gráfico+tabla (título siempre pegado al gráfico)
    """

    def __init__(self, output_path: str, generated_at: datetime | None = None):
        self.output_path  = output_path
        self.generated_at = generated_at or datetime.now()
        self.styles       = _styles()
        self.story: list  = []

    # ── Portada ──────────────────────────────────────────────────────────────

    def _add_cover(self, alert_report: AlertReport) -> None:
        s = self.styles
        self.story += [
            Spacer(1, 2.5 * cm),
            Paragraph("REPORTE AUTOMÁTICO", ParagraphStyle(
                "cover_eye", fontName="Helvetica", fontSize=11,
                textColor=C_BLUE_LIGHT, spaceAfter=2)),
            Paragraph("Pulse Reports — Trivia Campaign",
                      ParagraphStyle("cover_h1", fontName="Helvetica-Bold",
                                     fontSize=26, textColor=C_BLUE, spaceAfter=4)),
            HRFlowable(width="60%", thickness=3, color=C_ORANGE, spaceAfter=8 * mm),
            Paragraph(self.generated_at.strftime("%d de %B de %Y  ·  %H:%M"),
                      ParagraphStyle("cover_date", fontName="Helvetica",
                                     fontSize=12, textColor=colors.gray)),
            Spacer(1, 1.5 * cm),
        ]
        if alert_report.alerts:
            total    = len(alert_report.alerts)
            criticas = len(alert_report.by_level("critica"))
            adverts  = len(alert_report.by_level("advertencia"))
            self.story.append(Paragraph(
                f"<b>{total} alerta(s)</b> detectadas: "
                f"<font color='red'>{criticas} crítica(s)</font>  ·  "
                f"<font color='orange'>{adverts} advertencia(s)</font>",
                s["body"],
            ))
        else:
            self.story.append(Paragraph("✅ Sin alertas activas.", s["body"]))
        self.story.append(PageBreak())

    # ── Resumen ejecutivo ─────────────────────────────────────────────────────

    def add_kpi_cards(self, df_hoy_hn: pd.DataFrame, df_hoy_sv: pd.DataFrame,
                      df_triv_hn: pd.DataFrame, df_triv_sv: pd.DataFrame) -> None:
        s = self.styles

        def _val(df: pd.DataFrame, col: str) -> str:
            if df.empty or col not in df.columns:
                return "—"
            v = df[col].sum()
            try:
                return f"{int(v):,}"
            except (ValueError, TypeError):
                return "—"

        kpis = [
            ["KPI", "Honduras", "El Salvador"],
            ["🎁 Premios entregados hoy",
             _val(df_hoy_hn, "entregados_hoy"), _val(df_hoy_sv, "entregados_hoy")],
            ["🎮 Trivias jugadas",
             _val(df_triv_hn, "veces_jugado"), _val(df_triv_sv, "veces_jugado")],
            ["✅ Respuestas correctas",
             _val(df_triv_hn, "correctas"), _val(df_triv_sv, "correctas")],
            ["❌ Respuestas incorrectas",
             _val(df_triv_hn, "incorrectas"), _val(df_triv_sv, "incorrectas")],
        ]
        t = Table(kpis, colWidths=[7 * cm, 4.5 * cm, 4.5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 8),
            ("FONTNAME",      (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_GRAY_LIGHT]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_GRAY_MID),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        self.story += [
            Paragraph("📊 Resumen Ejecutivo", s["h2"]),
            HRFlowable(width="100%", thickness=1.5, color=C_BLUE_LIGHT,
                       spaceAfter=3 * mm),
            t,
        ]

    # ── Alertas (continúa en la misma página del resumen) ────────────────────

    def add_alerts_section(self, alert_report: AlertReport) -> None:
        s = self.styles
        self.story += [
            Spacer(1, 5 * mm),
            Paragraph("⚠ Alertas del Sistema", s["h2"]),
            HRFlowable(width="100%", thickness=1.5, color=C_BLUE_LIGHT,
                       spaceAfter=3 * mm),
        ]
        if not alert_report.alerts:
            self.story.append(Paragraph("Sin alertas activas.", s["body"]))
            return
        for a in alert_report.alerts:
            style_key = {"critica": "alert_crit",
                         "advertencia": "alert_warn"}.get(a.level, "alert_info")
            self.story.append(
                Paragraph(f"{a.icon}  <b>{a.title}</b> — {a.detail}", s[style_key])
            )

    # ── Trivias ───────────────────────────────────────────────────────────────

    def add_trivias_section(self, df_hn: pd.DataFrame, df_sv: pd.DataFrame,
                             chart_rend_hn, chart_rend_sv,
                             chart_part_hn, chart_part_sv) -> None:
        s = self.styles
        SECCION = "🎮 Rendimiento de Trivias"

        for pais, df, c_rend, c_part in [
            ("Honduras",    df_hn, chart_rend_hn, chart_part_hn),
            ("El Salvador", df_sv, chart_rend_sv, chart_part_sv),
        ]:
            cols_show = [c for c in
                         ["id", "trivia", "enviados", "abiertos", "veces_jugado",
                          "correctas", "incorrectas"] if c in df.columns]
            tbl = _df_to_table(
                df[cols_show].rename(columns={
                    "id": "ID", "trivia": "Trivia", "enviados": "Enviados",
                    "abiertos": "Abiertos", "veces_jugado": "Jugado",
                    "correctas": "Correctas", "incorrectas": "Incorrectas",
                }),
                col_widths=[0.06, 0.30, 0.11, 0.11, 0.10, 0.12, 0.12],
            ) if (not df.empty and cols_show) else None

            # Página: gráfico correctas/incorrectas + tabla
            self.story += _chart_page(
                SECCION, f"{pais}  —  Correctas vs Incorrectas",
                c_rend, tbl, s,
                note="" if not df.empty else "Sin datos disponibles.",
            )
            # Página: gráfico participación
            self.story += _chart_page(
                SECCION, f"{pais}  —  Participación (Top 15)",
                c_part, None, s,
            )

    # ── Premios hoy ───────────────────────────────────────────────────────────

    def add_premios_hoy_section(self, df_hn: pd.DataFrame, df_sv: pd.DataFrame,
                                 chart_hn, chart_sv,
                                 df_combinado: pd.DataFrame | None = None) -> None:
        s = self.styles
        SECCION = "🎁 Premios Entregados Hoy"

        # ── Tabla consolidada: País + Premio + Fecha + Entregados ────────────
        if df_combinado is not None and not df_combinado.empty:
            cols = [c for c in ["pais", "premio", "fecha", "entregados_hoy"]
                    if c in df_combinado.columns]
            tbl_consolidada = _df_to_table(
                df_combinado[cols].rename(columns={
                    "pais": "País", "premio": "Premio",
                    "fecha": "Fecha", "entregados_hoy": "Entregados Hoy",
                }),
                col_widths=[0.18, 0.47, 0.15, 0.20],
            )
            self.story += [
                PageBreak(),
                KeepTogether(
                    _page_title(SECCION, "Detalle consolidado — Honduras + El Salvador", s)
                ),
                tbl_consolidada,
            ]
        else:
            self.story += [
                PageBreak(),
                KeepTogether(
                    _page_title(SECCION, "Detalle consolidado — Honduras + El Salvador", s) +
                    [Paragraph("Sin entregas registradas hoy en ningún país.", s["small"])]
                ),
            ]

        for pais, df, chart in [
            ("Honduras",    df_hn, chart_hn),
            ("El Salvador", df_sv, chart_sv),
        ]:
            tbl = _df_to_table(
                df[[c for c in ["premio", "entregados_hoy", "fecha"] if c in df.columns]].rename(
                    columns={"premio": "Premio", "entregados_hoy": "Entregados Hoy",
                             "fecha": "Fecha"}),
                col_widths=[0.55, 0.25, 0.20] if "fecha" in df.columns else [0.75, 0.25],
            ) if (not df.empty and "premio" in df.columns) else None

            self.story += _chart_page(
                SECCION, pais, chart, tbl, s,
                note="" if not df.empty else "Sin entregas registradas hoy.",
            )

    # ── Histórico ─────────────────────────────────────────────────────────────

    def add_historico_section(self, df_hn: pd.DataFrame, df_sv: pd.DataFrame,
                               chart_hn, chart_sv) -> None:
        s = self.styles
        SECCION = "📈 Histórico y Promedio de Premios"

        for pais, df, chart in [
            ("Honduras",    df_hn, chart_hn),
            ("El Salvador", df_sv, chart_sv),
        ]:
            cols = [c for c in
                    ["premio", "entregados_ayer", "entregados_total", "promedio_x_dia"]
                    if c in df.columns]
            tbl = _df_to_table(
                df[cols].rename(columns={
                    "premio": "Premio", "entregados_ayer": "Ayer",
                    "entregados_total": "Total", "promedio_x_dia": "Prom/día",
                }),
                col_widths=[0.55, 0.15, 0.15, 0.15],
            ) if (not df.empty and cols) else None

            self.story += _chart_page(
                SECCION, pais, chart, tbl, s,
                note="" if not df.empty else "Sin datos.",
            )

    # ── Config GP ─────────────────────────────────────────────────────────────

    def add_config_gp_section(self, df_hn: pd.DataFrame, df_sv: pd.DataFrame,
                               chart_hn, chart_sv) -> None:
        s = self.styles
        SECCION = "⚙ Configuración GP — Stock y Premios"

        for pais, df, chart in [
            ("Honduras",    df_hn, chart_hn),
            ("El Salvador", df_sv, chart_sv),
        ]:
            cols = [c for c in
                    ["premio", "tipo", "prob", "alcance", "stock_total",
                     "ganados", "faltan_por_entregar", "tiendas"]
                    if c in df.columns]
            tbl = _df_to_table(
                df[cols].rename(columns={
                    "premio": "Premio", "tipo": "Tipo", "prob": "Prob",
                    "alcance": "Alcance", "stock_total": "Stock",
                    "ganados": "Ganados", "faltan_por_entregar": "Faltan",
                    "tiendas": "Tiendas",
                }),
                col_widths=[0.30, 0.09, 0.07, 0.10, 0.10, 0.10, 0.10, 0.08],
            ) if (not df.empty and cols) else None

            self.story += _chart_page(
                SECCION, pais, chart, tbl, s,
                note="" if not df.empty else "Sin datos.",
            )

    # ── Estado reclamos ───────────────────────────────────────────────────────

    def add_reclamos_section(self, df: pd.DataFrame, chart) -> None:
        s = self.styles
        SECCION = "📋 Estado de Premios Reclamados"

        if df.empty:
            self.story += [
                PageBreak(),
                KeepTogether(
                    _page_title(SECCION, "Honduras + El Salvador", s) +
                    [Paragraph("Sin reclamos registrados.", s["small"])]
                ),
            ]
            return

        resumen = (df["estado_premio"].value_counts()
                   .reset_index()
                   .rename(columns={"estado_premio": "Estado", "count": "Cantidad"}))
        tbl = _df_to_table(resumen, col_widths=[0.75, 0.25])

        self.story += _chart_page(
            SECCION, "Honduras + El Salvador",
            chart, tbl, s,
            note="Distribución de estados de premios reclamados.",
        )

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, alert_report: AlertReport) -> None:
        os.makedirs(os.path.dirname(self.output_path) or ".", exist_ok=True)
        doc = BaseDocTemplate(
            self.output_path,
            pagesize=A4,
            rightMargin=1.2 * cm,
            leftMargin=1.2 * cm,
            topMargin=2.2 * cm,
            bottomMargin=1.2 * cm,
        )
        doc.addPageTemplates(_make_page_templates(doc))
        self._add_cover(alert_report)
        doc.build(self.story)
        logger.info("PDF generado: %s", self.output_path)