"""
reports/reporte_diario_mensaje.py
Construye el mensaje de texto de Telegram del reporte diario Gane Premios.
"""
import pandas as pd

_FLAGS = {"Guatemala": "🇬🇹", "El Salvador": "🇸🇻", "Honduras": "🇭🇳"}


def _hora(txt: str) -> str:
    """'03:45 PM' -> '03:45 p. m.'"""
    hhmm, ampm = txt.strip().split()
    return f"{hhmm} {'a. m.' if ampm.upper() == 'AM' else 'p. m.'}"


def _valor(value) -> str:
    if value is None or pd.isna(value):
        return "No disponible"
    return f"{int(value):,}"


def _cantidad(value) -> str:
    return _valor(value)


def _desglose_arrastres(breakdown) -> str:
    if breakdown is None:
        return "No disponible"
    if not breakdown:
        return "0"
    return ", ".join(
        f"{start:%d/%m/%Y}: {breakdown[start]}"
        for start in sorted(breakdown)
    )


def build_mensaje(df: pd.DataFrame) -> str:
    """Construye un resumen breve con programación actual y adjudicaciones."""
    fecha = pd.to_datetime(df.iloc[0]["fecha"]).strftime("%d/%m/%Y")
    lines = [
        "🏆 REPORTE DIARIO – GANE PREMIOS",
        "",
        f"📅 Fecha: {fecha}",
        f"🕐 Hora de corte: {_hora(df.iloc[0]['hora_corte'])}",
    ]
    for r in df.itertuples():
        country_lines = [
            "",
            f"{_FLAGS[r.pais]} {r.pais.upper()}",
            f"🎯 Programado para hoy: "
            f"{_cantidad(r.programado_hoy_config_vigente)}",
            f"   🆕 Nuevos: {_cantidad(r.programados_nuevos_hoy)}",
            f"   🔁 Programados arrastrados: {_cantidad(r.programados_arrastrados_hoy)} "
            f"(inicio original: {_desglose_arrastres(r.arrastrados_por_fecha_inicio)})",
            f"📈 Premios programados acumulados al cierre de hoy: "
            f"{_cantidad(r.programado_acumulado_config_vigente)}",
            f"✅ Premios adjudicados hoy al corte: {_valor(r.premios_adjudicados_hoy)}",
            f"🏆 Premios adjudicados acumulados al corte (incluye hoy): "
            f"{_valor(r.premios_adjudicados_acumulados)}",
        ]
        later_end = r.programados_vigentes_fin_posterior_hoy
        if pd.isna(later_end) or later_end is None:
            country_lines.insert(
                6, "   📅 Vigentes hoy, fin posterior (fuera del total): No disponible")
        elif later_end > 0:
            country_lines.insert(
                6, f"   📅 Vigentes hoy, fin posterior (fuera del total): {_cantidad(later_end)}")
        lines += country_lines
    lines += [
        "",
        "📊 Fuente: Grupo UNO · Pulse Reports",
    ]
    return "\n".join(lines)
