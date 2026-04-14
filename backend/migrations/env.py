import os
import re
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Tables Alembic must NOT manage:
#  - Postgres partition children of `events` (events_yYYYYmMM, events_default) —
#    managed by partition DDL in bb1_events_partitioning, not by models.
#  - events_legacy — intentional rollback safety net from bb1_events_partitioning.
#  - merchant_email_stats — intentional raw-SQL-only table (no SQLAlchemy model
#    on purpose; consumed by email_performance / action_learning / churn predictor).
_EVENTS_PARTITION_RE = re.compile(r"^events_(default|y\d{4}m\d{2})$")
_UNMANAGED_TABLES = {"events_legacy", "merchant_email_stats"}


def _is_unmanaged(name: str | None) -> bool:
    if not name:
        return False
    if name in _UNMANAGED_TABLES:
        return True
    if _EVENTS_PARTITION_RE.match(name):
        return True
    return False


def _include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and _is_unmanaged(name):
        return False
    if type_ in {"index", "unique_constraint", "foreign_key_constraint", "column"} and obj is not None:
        tbl = getattr(obj, "table", None)
        tname = getattr(tbl, "name", None) if tbl is not None else None
        if _is_unmanaged(tname):
            return False
    return True


def _include_name(name, type_, parent_names):
    if type_ == "table" and _is_unmanaged(name):
        return False
    return True

# Load .env so DATABASE_URL is available
load_dotenv()

# Alembic Config object
config = context.config

# Override sqlalchemy.url from environment — never read credentials from alembic.ini
database_url = os.getenv(
    "DATABASE_URL",
    "postgresql://aiuser:aipassword@localhost:5432/wishspark"
)
config.set_main_option("sqlalchemy.url", database_url)

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import Base and all models so their tables are registered in metadata.
# app/models/__init__.py is the single source of truth for model registration.
from app.core.database import Base
import app.models  # noqa: F401 — registers all models on Base.metadata

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        include_name=_include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
            include_name=_include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
