"""
config/settings.py
Carga y valida toda la configuración desde variables de entorno.
"""
import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DatabaseConfig:
    host: str
    port: int
    name: str
    user: str
    password: str

    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.name} "
            f"user={self.user} password={self.password} "
            "connect_timeout=10 application_name=gp_reporting"
        )


@dataclass
class NotificationConfig:
    teams_webhook: Optional[str]
    slack_webhook: Optional[str]
    telegram_token: Optional[str]
    telegram_chat_id: Optional[str]
    email_smtp_host: str
    email_smtp_port: int
    email_user: Optional[str]
    email_pass: Optional[str]
    email_recipients: list[str]


@dataclass
class AlertConfig:
    stock_threshold: float      # fracción p. ej. 0.10
    trivia_drop_threshold: float


@dataclass
class SchedulerConfig:
    interval_minutes: int
    daily_report_time: str      # "HH:MM"


@dataclass
class AppConfig:
    db_hn: DatabaseConfig
    db_sv: DatabaseConfig
    notifications: NotificationConfig
    alerts: AlertConfig
    scheduler: SchedulerConfig
    output_dir: str = "output"

    # IDs fijos de formularios
    trivia_ids_hn: list[int] = field(default_factory=lambda: [
        93,94,95,96,97,98,99,100,101,102,
        123,124,125,126,127,128,129,130,131,132,
        143,144,145,146,147,148,149,150,151,152,153
    ])
    trivia_ids_sv: list[int] = field(default_factory=lambda: [
        113,114,115,116,117,118,119,120,121,122,
        133,134,135,136,137,138,139,140,141,142,
        154,155,156,157,158,159,160,161,162,163,164
    ])
    reward_ids_hn: list[int] = field(default_factory=lambda: [280,282,283,284])
    reward_ids_sv: list[int] = field(default_factory=lambda: [298,300,301,302,303,304,305])
    campaign_ids_hn: list[int] = field(default_factory=lambda: [376,378])
    campaign_ids_sv: list[int] = field(default_factory=lambda: [406,408])
    all_reward_ids: list[int] = field(default_factory=lambda: [
        280,282,283,284,285,298,299,300,301,302,303,304,305
    ])


def load_config() -> AppConfig:
    def _get(key: str, default: str = "") -> str:
        return os.getenv(key, default).strip()

    def _get_int(key: str, default: int = 0) -> int:
        try:
            return int(_get(key, str(default)))
        except ValueError:
            return default

    def _get_float(key: str, default: float = 0.0) -> float:
        try:
            return float(_get(key, str(default)))
        except ValueError:
            return default

    recipients_raw = _get("EMAIL_RECIPIENTS", "")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    return AppConfig(
        db_hn=DatabaseConfig(
            host=_get("DB_HN_HOST", "localhost"),
            port=_get_int("DB_HN_PORT", 5432),
            name=_get("DB_HN_NAME"),
            user=_get("DB_HN_USER"),
            password=_get("DB_HN_PASS"),
        ),
        db_sv=DatabaseConfig(
            host=_get("DB_SV_HOST", "localhost"),
            port=_get_int("DB_SV_PORT", 5432),
            name=_get("DB_SV_NAME"),
            user=_get("DB_SV_USER"),
            password=_get("DB_SV_PASS"),
        ),
        notifications=NotificationConfig(
            teams_webhook=_get("TEAMS_WEBHOOK_URL") or None,
            slack_webhook=_get("SLACK_WEBHOOK_URL") or None,
            telegram_token=_get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=_get("TELEGRAM_CHAT_ID") or None,
            email_smtp_host=_get("EMAIL_SMTP_HOST", "smtp.gmail.com"),
            email_smtp_port=_get_int("EMAIL_SMTP_PORT", 587),
            email_user=_get("EMAIL_USER") or None,
            email_pass=_get("EMAIL_PASS") or None,
            email_recipients=recipients,
        ),
        alerts=AlertConfig(
            stock_threshold=_get_float("STOCK_ALERT_THRESHOLD", 0.10),
            trivia_drop_threshold=_get_float("TRIVIA_DROP_THRESHOLD", 0.25),
        ),
        scheduler=SchedulerConfig(
            interval_minutes=_get_int("REPORT_INTERVAL_MINUTES", 60),
            daily_report_time=_get("DAILY_REPORT_TIME", "08:00"),
        ),
    )


# Instancia global
config = load_config()
