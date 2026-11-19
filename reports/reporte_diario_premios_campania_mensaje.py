"""Formato del reporte diario de premios desglosado por campaña."""
from zoneinfo import ZoneInfo

from queries.reporte_diario_premios import TZ

FLAGS = {"Guatemala": "🇬🇹", "El Salvador": "🇸🇻", "Honduras": "🇭🇳"}


def _quantity(value):
    return "No disponible" if value is None else f"{value:,}"


def _rollover_dates(breakdown):
    if breakdown is None:
        return "No disponible"
    if not breakdown:
        return "0"
    return ", ".join(
        f"{day:%d/%m/%Y}: {breakdown[day]}"
        for day in sorted(breakdown)
    )


def build_mensaje_por_campania(report):
    cutoff = report["fecha_hora_corte"].astimezone(ZoneInfo(TZ))
    lines = [
        "🏆 REPORTE DIARIO – GANE PREMIOS POR CAMPAÑA",
        "",
        f"📅 Fecha: {cutoff:%d/%m/%Y}",
        f"🕐 Hora de corte: {cutoff:%I:%M %p}".replace("AM", "a. m.").replace("PM", "p. m."),
    ]

    campaigns_by_country = {}
    for campaign in report["campanias"]:
        campaigns_by_country.setdefault(campaign["pais"], []).append(campaign)
    countries = {country["pais"]: country for country in report["paises"]}

    for country, flag in FLAGS.items():
        summary = countries[country]
        lines += [
            "",
            f"{flag} {country.upper()} — TOTAL",
            f"🎯 Programado para hoy: {_quantity(summary['programado_hoy_config_vigente'])}",
            f"   🆕 Nuevos: {_quantity(summary['programados_nuevos_hoy'])}",
            f"   🔁 Programados arrastrados: "
            f"{_quantity(summary['programados_arrastrados_hoy'])} "
            f"(inicio original: {_rollover_dates(summary['arrastrados_por_fecha_inicio'])})",
            f"📈 Programados acumulados al cierre de hoy: "
            f"{_quantity(summary['programado_acumulado_config_vigente'])}",
            f"✅ Adjudicados hoy al corte: {_quantity(summary['adjudicados_hoy_al_corte'])}",
            f"🏆 Adjudicados acumulados al corte: "
            f"{_quantity(summary['adjudicados_acumulados_al_corte'])}",
        ]
        if summary["programados_vigentes_fin_posterior_hoy"] not in (None, 0):
            lines.append(
                "   📅 Vigentes hoy, fin posterior (fuera del total): "
                f"{_quantity(summary['programados_vigentes_fin_posterior_hoy'])}"
            )

        for campaign in campaigns_by_country[country]:
            lines += [
                "",
                f"📌 Campaña {campaign['campaign_id']}",
                f"🎯 Programado para hoy: "
                f"{_quantity(campaign['programado_hoy_config_vigente'])}",
                f"   🆕 Nuevos: {_quantity(campaign['programados_nuevos_hoy'])}",
                f"   🔁 Programados arrastrados: "
                f"{_quantity(campaign['programados_arrastrados_hoy'])} "
                f"(inicio original: {_rollover_dates(campaign['arrastrados_por_fecha_inicio'])})",
                f"📈 Programados acumulados al cierre de hoy: "
                f"{_quantity(campaign['programado_acumulado_config_vigente'])}",
                f"✅ Adjudicados hoy al corte: "
                f"{_quantity(campaign['adjudicados_hoy_al_corte'])}",
                f"🏆 Adjudicados acumulados al corte: "
                f"{_quantity(campaign['adjudicados_acumulados_al_corte'])}",
            ]
            if campaign["programados_vigentes_fin_posterior_hoy"] not in (None, 0):
                lines.append(
                    "   📅 Vigentes hoy, fin posterior (fuera del total): "
                    f"{_quantity(campaign['programados_vigentes_fin_posterior_hoy'])}"
                )
    lines += [
        "",
        "La programación suma qty de la configuración vigente; no representa saldo pendiente.",
        "Adjudicaciones: CustomerPrizeWins; no equivalen a entrega física.",
    ]
    return "\n".join(lines)
