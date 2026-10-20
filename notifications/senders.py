"""
notifications/senders.py
Envío de notificaciones por Teams, Slack, Telegram y Email.
"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import requests

from config.settings import NotificationConfig
from reports.alerts import AlertReport

logger = logging.getLogger(__name__)

TIMEOUT = 10   # segundos


# ─────────────────────────────────────────────────────────────────────────────
# Texto de resumen para mensajes cortos
# ─────────────────────────────────────────────────────────────────────────────

def build_summary(
    alert_report: AlertReport,
    df_hoy_hn,
    df_hoy_sv,
    df_triv_hn,
    df_triv_sv,
    generated_at: datetime | None = None,
    df_premios_hoy_detalle=None,
) -> str:
    ts = (generated_at or datetime.now()).strftime("%d/%m/%Y %H:%M")
    fecha = (generated_at or datetime.now()).strftime("%d/%m/%Y")

    def _sum(df, col: str) -> str:
        if df is None or df.empty or col not in df.columns:
            return "—"
        return f"{int(df[col].sum()):,}"

    def _detalle_premios_hoy(df) -> list[str]:
        """Lista cada premio entregado hoy, agrupado por país."""
        if df is None or df.empty:
            return ["  (sin entregas registradas hoy)"]
        out: list[str] = []
        flags = {"Honduras": "🇭🇳", "El Salvador": "🇸🇻"}
        for pais, grupo in df.groupby("pais", sort=True):
            out.append(f"  {flags.get(pais, '')} *{pais}*")
            grupo_ordenado = grupo.sort_values("entregados_hoy", ascending=False)
            for _, row in grupo_ordenado.iterrows():
                out.append(f"    • {row['premio']} — {int(row['entregados_hoy']):,}")
        return out

    lines = [
        "📊 *Pulse Reports*",
        "",
        f"⏱️_{ts}_",
        "",
        "🇭🇳 *Honduras*",
        f"  🎁 Premios entregados hoy: {_sum(df_hoy_hn, 'entregados_hoy')}",
        f"  🎮 Trivias jugadas: {_sum(df_triv_hn, 'veces_jugado')}",
        f"  ✅ Correctas: {_sum(df_triv_hn, 'correctas')}",
        f"  ❌ Incorrectas: {_sum(df_triv_hn, 'incorrectas')}",
        "",
        "🇸🇻 *El Salvador*",
        f"  🎁 Premios entregados hoy: {_sum(df_hoy_sv, 'entregados_hoy')}",
        f"  🎮 Trivias jugadas: {_sum(df_triv_sv, 'veces_jugado')}",
        f"  ✅ Correctas: {_sum(df_triv_sv, 'correctas')}",
        f"  ❌ Incorrectas: {_sum(df_triv_sv, 'incorrectas')}",
        "",
        f"🎁 *Detalle de premios entregados hoy ({fecha})*",
        *_detalle_premios_hoy(df_premios_hoy_detalle),
        "",
        alert_report.summary_text(),
        "",
        "📄 PDF completo adjunto.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Teams
# ─────────────────────────────────────────────────────────────────────────────

def send_teams(cfg: NotificationConfig, summary: str,
               pdf_path: Optional[str] = None) -> bool:
    if not cfg.teams_webhook:
        logger.debug("Teams no configurado, omitiendo.")
        return False
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "1A3C6E",
        "summary": "Reporte GP",
        "sections": [{"text": summary.replace("\n", "<br>")}],
    }
    if pdf_path and Path(pdf_path).exists():
        payload["potentialAction"] = [{
            "@type": "OpenUri",
            "name": "Ver PDF (requiere acceso al servidor)",
            "targets": [{"os": "default", "uri": pdf_path}],
        }]
    try:
        r = requests.post(
            cfg.teams_webhook,
            json=payload,
            timeout=TIMEOUT,
        )

        print("=" * 80)
        print("STATUS:", r.status_code)
        print("RESPONSE:")
        print(r.text)
        print("=" * 80)

        r.raise_for_status()

        logger.info("Teams: notificación enviada.")
        return True
    except Exception as exc:
        logger.error("Teams: error al enviar — %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Slack
# ─────────────────────────────────────────────────────────────────────────────

def send_slack(cfg: NotificationConfig, summary: str) -> bool:
    if not cfg.slack_webhook:
        logger.debug("Slack no configurado, omitiendo.")
        return False
    try:
        r = requests.post(
            cfg.slack_webhook,
            json={"text": summary},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        logger.info("Slack: notificación enviada.")
        return True
    except Exception as exc:
        logger.error("Slack: error al enviar — %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram(cfg: NotificationConfig, summary: str,
                  pdf_path: Optional[str] = None) -> bool:
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        logger.debug("Telegram no configurado, omitiendo.")
        return False
    base_url = f"https://api.telegram.org/bot{cfg.telegram_token}"
    try:
        # Mensaje de texto
        r = requests.post(
            f"{base_url}/sendMessage",
            data={
                "chat_id": cfg.telegram_chat_id,
                "text": summary,
                "parse_mode": "Markdown",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()

        # Envío del PDF como documento
        if pdf_path and Path(pdf_path).exists():
            with open(pdf_path, "rb") as f:
                r2 = requests.post(
                    f"{base_url}/sendDocument",
                    data={"chat_id": cfg.telegram_chat_id,
                          "caption": "📄 Reporte completo"},
                    files={"document": f},
                    timeout=60,
                )
                r2.raise_for_status()

        logger.info("Telegram: notificación enviada.")
        return True
    except Exception as exc:
        logger.error("Telegram: error al enviar — %s", exc)
        return False


class TelegramError(Exception):
    """Error de Telegram con mensaje seguro (sin token ni chat_id)."""


def _tg_call(cfg: NotificationConfig, method: str, timeout: int, **kwargs) -> dict:
    """Una sola llamada a la Bot API. No reintenta y nunca incluye la URL
    (que contiene el token) en los errores."""
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/{method}"
    try:
        r = requests.post(url, timeout=timeout, **kwargs)
    except requests.Timeout:
        raise TelegramError(f"{method}: tiempo de espera agotado (el envío pudo completarse; "
                            "verifica el grupo antes de reintentar)") from None
    except requests.RequestException as exc:
        raise TelegramError(f"{method}: error de red ({type(exc).__name__})") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not r.ok or not body.get("ok"):
        raise TelegramError(f"{method}: HTTP {r.status_code} — "
                            f"{body.get('description', 'respuesta no válida')}")
    return body["result"]


def get_telegram_chat(cfg: NotificationConfig) -> dict:
    """Consulta (solo lectura) el chat configurado: tipo, título y nº de miembros."""
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        raise TelegramError("Telegram no está configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    chat = _tg_call(cfg, "getChat", TIMEOUT, data={"chat_id": cfg.telegram_chat_id})
    try:
        chat["members"] = _tg_call(cfg, "getChatMemberCount", TIMEOUT,
                                   data={"chat_id": cfg.telegram_chat_id})
    except TelegramError:
        chat["members"] = None
    return chat


def send_telegram_document(cfg: NotificationConfig, file_path: str, caption: str) -> dict:
    """Envía un archivo con caption en UN solo mensaje (sin reintentos, para
    evitar duplicados). Devuelve el mensaje de Telegram; lanza TelegramError si falla.
    El caption (máx. 1024) se envía como texto plano."""
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        raise TelegramError("Telegram no está configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    path = Path(file_path)
    if not path.is_file():
        raise TelegramError(f"El archivo no existe: {path}")
    if len(caption) > 1024:
        raise TelegramError("El mensaje excede los 1024 caracteres permitidos en un caption.")
    with open(path, "rb") as f:
        msg = _tg_call(cfg, "sendDocument", 120,
                       data={"chat_id": cfg.telegram_chat_id, "caption": caption},
                       files={"document": (path.name, f)})
    if not msg.get("document"):
        raise TelegramError("Telegram respondió sin confirmar el documento adjunto.")
    return msg


def send_telegram_text(cfg: NotificationConfig, text: str) -> dict:
    """Envía UN mensaje de texto plano (sendMessage) sin reintentos, para evitar
    duplicados. Lanza TelegramError si falla."""
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        raise TelegramError("Telegram no está configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    if not text.strip() or len(text) > 4096:
        raise TelegramError("El mensaje está vacío o excede los 4096 caracteres de Telegram.")
    return _tg_call(cfg, "sendMessage", TIMEOUT,
                    data={"chat_id": cfg.telegram_chat_id, "text": text})


# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────

def send_email(cfg: NotificationConfig, summary: str,
               pdf_path: Optional[str] = None,
               generated_at: datetime | None = None) -> bool:
    if not cfg.email_user or not cfg.email_pass or not cfg.email_recipients:
        logger.debug("Email no configurado, omitiendo.")
        return False
    ts = (generated_at or datetime.now()).strftime("%d/%m/%Y %H:%M")
    msg = EmailMessage()
    msg["Subject"] = f"Pulse Reports — {ts}"
    msg["From"]    = cfg.email_user
    msg["To"]      = ", ".join(cfg.email_recipients)
    msg.set_content(summary)           # Texto plano

    # HTML enriquecido
    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;font-size:14px;color:#2C3E50">
    <div style="background:#1A3C6E;color:white;padding:16px 24px;border-radius:6px 6px 0 0">
      <h2 style="margin:0">📊 Pulse Reports — {ts}</h2>
    </div>
    <div style="padding:16px 24px;background:#F8FAFC;border:1px solid #EEF2F7">
      <pre style="white-space:pre-wrap;font-size:13px">{summary}</pre>
    </div>
    <div style="padding:10px 24px;background:#EEF2F7;font-size:11px;color:#95A5A6;
                border-radius:0 0 6px 6px">
      Generado automáticamente por Pulse Reports.
    </div>
    </body></html>
    """
    msg.add_alternative(html_body, subtype="html")

    # Adjuntar PDF
    if pdf_path and Path(pdf_path).exists():
        ctype, _ = mimetypes.guess_type(pdf_path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(pdf_path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=Path(pdf_path).name)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg.email_smtp_host, cfg.email_smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(cfg.email_user, cfg.email_pass)
            server.sendmail(cfg.email_user, cfg.email_recipients, msg.as_bytes())
        logger.info("Email enviado a: %s", cfg.email_recipients)
        return True
    except Exception as exc:
        logger.error("Email: error al enviar — %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_all(cfg: NotificationConfig, summary: str,
                 pdf_path: Optional[str] = None,
                 generated_at: datetime | None = None) -> None:
    """Envía por todos los canales configurados."""
    results = {
        "teams":    send_teams(cfg, summary, pdf_path),
        "slack":    send_slack(cfg, summary),
        "telegram": send_telegram(cfg, summary, pdf_path),
        "email":    send_email(cfg, summary, pdf_path, generated_at),
    }
    enviados = [k for k, v in results.items() if v]
    omitidos = [k for k, v in results.items() if not v]
    logger.info("Notificaciones enviadas: %s | Omitidas/fallidas: %s",
                enviados or "ninguna", omitidos or "ninguna")