import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
project_root = Path(config.config_file_name).resolve().parent
sys.path.insert(0, str(project_root))
from app import config as app_config  # noqa: E402

database_url = URL.create(
    "postgresql+psycopg2",
    username=app_config.PG_USER,
    password=app_config.PG_PASSWORD,
    host=app_config.PG_HOST,
    port=app_config.PG_PORT,
    database=app_config.PG_DB,
)
target_metadata = None


def run_migrations_offline():
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
