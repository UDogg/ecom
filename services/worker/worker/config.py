import os
from dataclasses import dataclass, field


def _env(name, default):
    return field(default_factory=lambda: os.environ.get(name, default))


def _delays():
    raw = os.environ.get("WORKER_RETRY_DELAYS_MS", "5000,30000,120000")
    return tuple(int(x) for x in raw.split(",") if x.strip())


@dataclass
class Config:
    db_host: str = _env("POSTGRES_HOST", "postgres")
    db_port: str = _env("POSTGRES_PORT", "5432")
    db_name: str = _env("POSTGRES_DB", "shop")
    db_user: str = _env("WORKER_DB_USER", "worker")
    db_password: str = _env("WORKER_DB_PASSWORD", "")
    db_schema: str = _env("WORKER_DB_SCHEMA", "worker")
    rabbitmq_url: str = _env("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
    exchange: str = _env("EVENTS_EXCHANGE", "commerce.events")
    queue_prefix: str = _env("WORKER_QUEUE_PREFIX", "worker")
    # One retry queue per delay; after the last one the message is dead-lettered.
    retry_delays_ms: tuple = field(default_factory=_delays)
    prefetch: int = field(default_factory=lambda: int(os.environ.get("WORKER_PREFETCH", "10")))
    smtp_host: str = _env("SMTP_HOST", "mailpit")
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "1025")))
    mail_from: str = _env("MAIL_FROM", "orders@shop.local")
    owner_alert_email: str = _env("OWNER_ALERT_EMAIL", "owner@shop.local")
    low_stock_threshold: int = field(default_factory=lambda: int(os.environ.get("LOW_STOCK_THRESHOLD", "5")))
    public_web_url: str = _env("PUBLIC_WEB_URL", "http://localhost:3000")
    health_port: int = field(default_factory=lambda: int(os.environ.get("HEALTH_PORT", "8090")))
    max_backoff_seconds: float = field(default_factory=lambda: float(os.environ.get("WORKER_MAX_BACKOFF_SECONDS", "30")))

    @property
    def max_attempts(self):
        return len(self.retry_delays_ms) + 1

    @property
    def dsn(self):
        import psycopg

        return psycopg.conninfo.make_conninfo(host=self.db_host, port=self.db_port, dbname=self.db_name,
                                              user=self.db_user, password=self.db_password, connect_timeout=5)
