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


def build_mensaje(df: pd.DataFrame, quemados: dict = None, advertencias: list = None) -> str:
    """`quemados`: acumulado manual por país (obligatorio si se muestra el indicador)."""
    fecha = pd.to_datetime(df.iloc[0]["fecha"]).strftime("%d/%m/%Y")
    lines = [
        "🏆 REPORTE DIARIO – GANE PREMIOS",
        "",
        f"📅 Fecha: {fecha}",
        f"🕐 Hora de corte: {_hora(df.iloc[0]['hora_corte'])}",
    ]
    for r in df.itertuples():
        lines += [
            "",
            f"{_FLAGS[r.pais]} {r.pais.upper()}",
            f"🎯 Premios a entregar hoy: {r.premios_a_entregar_total}",
            f"⏱️ Premios habilitados al corte: {r.premios_a_entregar_corte}",
            f"✅ Premios entregados hoy: {r.premios_entregados_hoy}",
            f"🔥 Premios habilitados acumulados: {quemados[r.pais]}",
            f"🏆 Total entregados acumulados: {r.total_entregados_acumulados}",
        ]
    for warning in advertencias or []:
        lines += ["", f"⚠️ {warning}"]
    lines += ["", "📊 Fuente: Grupo UNO · Pulse Reports"]
    return "\n".join(lines)
