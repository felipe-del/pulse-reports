"""Formato del dashboard de programación configurada de premios por país."""
from datetime import datetime
from zoneinfo import ZoneInfo

from queries.reporte_diario_premios import TZ

COUNTRIES = (
    ("Honduras", "🇭🇳", (828, 829)),
    ("El Salvador", "🇸🇻", (825, 826)),
    ("Guatemala", "🇬🇹", (818, 819, 820)),
)

BALANCES_UNAVAILABLE = "N/D"
OVERDUE_UNAVAILABLE = "No disponible: falta saldo por PDV/programación."


def _show(value):
    if value is None:
        return BALANCES_UNAVAILABLE
    return f"{value:,}" if isinstance(value, (int, float)) else str(value)


def _inline(value):
    return "no disponible" if value is None else " ".join(str(value).split())


def _date_label(report_date, actual_today):
    if report_date == actual_today:
        return "hoy"
    return report_date.strftime("%d/%m/%Y")


def build_mensaje_premios_atrasados(report):
    cutoff = report["fecha_hora_corte"].astimezone(ZoneInfo(TZ))
    report_date = cutoff.date()
    actual_today = datetime.now(ZoneInfo(TZ)).date()
    date_label = _date_label(report_date, actual_today)
    lines = [
        "🏆 REPORTE DIARIO DE PREMIOS",
        f"📅 Fecha del reporte: {report_date:%d/%m/%Y}",
        f"🕐 Corte de datos: {cutoff:%d/%m/%Y %H:%M}",
        "",
        "Programación calculada desde qty de slot y configuración vigente; "
        "no representa entregados ni pendientes.",
    ]
    if report_date != actual_today:
        lines.append(
            "La fecha de referencia es pasada: se muestra el estado actual de la "
            "configuración al corte, no una captura histórica de esa fecha."
        )

    for country, flag, campaign_ids in COUNTRIES:
        country_metrics = report["paises"][country]
        lines += [
            "",
            f"{flag} {country.upper()}",
            "",
            f"📈 Premios programados hasta la fecha "
            f"(qty configurada): {_show(country_metrics['previstos_acumulados'])}",
            f"🎯 Premios programados para {date_label} "
            f"(qty configurada): {_show(country_metrics['previstos_hoy'])}",
            "🏆 Entregados de programaciones hasta la fecha: N/D",
            f"✅ Entregados de la programación de {date_label}: N/D",
            f"🟡 Pendientes de la programación de {date_label}: N/D",
            "⏳ Atrasados pendientes de días anteriores: N/D",
            "📦 Total pendiente hasta la fecha: N/D",
            "📊 Cumplimiento de programaciones hasta la fecha: N/D",
            "",
            "📋 POR CAMPAÑA",
        ]
        country_campaigns = {
            campaign["campaign_id"]: campaign
            for campaign in report["campanias"]
            if campaign["pais"] == country
        }
        for campaign_id in campaign_ids:
            campaign = country_campaigns[campaign_id]
            lines.append(
                f"• {campaign_id}: previstos hasta la fecha "
                f"(qty configurada) "
                f"{_show(campaign['previstos_acumulados'])} · entregados N/D · "
                f"pendientes de {date_label} N/D · atrasados N/D"
            )
        lines += [
            "",
            "📍 DETALLE DE ATRASADOS PENDIENTES",
            OVERDUE_UNAVAILABLE,
        ]

    lines += [
        "",
        "Limitación: la configuración no aporta saldos entregados/disponibles "
        "por PDV/programación; coupon.won es agregado y no se distribuye.",
    ]
    return "\n".join(lines)


def build_detalle_local(report):
    cutoff = report["fecha_hora_corte"].astimezone(ZoneInfo(TZ))
    lines = [
        "",
        "DETALLE LOCAL DE AUDITORÍA",
        f"Fecha de consulta: {cutoff:%d/%m/%Y %H:%M:%S %Z}",
        "La configuración no aporta un identificador estable de programación; "
        "no se deduplican slots.",
        "",
        "DIFERENCIAS AGREGADAS POR CUPÓN (una fila por cupón):",
    ]
    for coupon in report["cupones"]:
        lines.append(
            f"{coupon['pais']} | Campaña {coupon['campania']} | "
            f"cupón {coupon['coupon_id']} | premio {_show(coupon['premio'])} | "
            f"isWinner: {coupon['is_winner']} | isGlobal: {_show(coupon['is_global'])} | "
            f"qty configurada del cupón: {_show(coupon['qty_configurada_cupon'])} | "
            f"stock_total: {_show(coupon['stock_total_configuracion'])} | "
            f"won agregado: {_show(coupon['won_agregado_cupon'])} | "
            f"diferencia agregada (stock_total - won): "
            f"{_show(coupon['diferencia_agregada_cupon'])} | "
            f"fuentes: {_inline(coupon['fuente'])}"
        )

    lines += ["", "storeQty POR CUPÓN/TIENDA (no por fecha):"]
    for row in report["stocks_tienda"]:
        lines.append(
            f"{row['pais']} | Campaña {row['campania']} | cupón {row['coupon_id']} | "
            f"PDV ID: {_show(row['pdv_id'])} | Nombre: {_show(row['tienda_nombre'])} | "
            f"PDV válido: {row['pdv_valido']} | "
            f"storeQty configurado: {_show(row['storeQty_configurado'])} | "
            f"fuente: {row['fuente']}"
        )

    lines += ["", "SLOTS CONFIGURADOS (sin asignar stock ni won):"]
    for row in report["auditoria"]:
        if "fuentes" not in row:
            lines.append(
                f"{row['pais']} | Campaña {row['campania']} | "
                f"{row.get('configuracion', 'Sin slot')} | "
                f"fuente: {row.get('fuente', 'campaigns.push_data')}"
            )
            continue
        sources = "; ".join(
            f"{field}={source}" for field, source in row["fuentes"].items()
        )
        lines.append(
            f"{row['pais']} | Campaña {row['campania']} | "
            f"Campaña nombre: {_inline(row['campaign_name'])} | "
            f"Premio: {_inline(row['premio'])} | "
            f"cupón ID: {_show(row['coupon_id'])} | isWinner: {row['is_winner']} | "
            f"estado campaña: {_show(row['campaign_status'])} | "
            f"PDV ID: {_show(row['pdv_id'])} | "
            f"Nombre: {_inline(row['tienda_nombre'])} | "
            f"PDV válido: {row['pdv_valido']} | "
            f"fecha_inicio vigente: {_show(row['fecha_inicio'])} | "
            f"fecha_fin: {_show(row['fecha_fin'])} | "
            f"hora_inicio: {_show(row['hora_inicio'])} | "
            f"hora_fin: {_show(row['hora_fin'])} | "
            f"qty slot configurada (no saldo): "
            f"{_show(row['cantidad_slot_configurada'])} | "
            f"entregados/disponibles/días de atraso: N/D | fuentes: {sources}"
        )
    return "\n".join(lines)
