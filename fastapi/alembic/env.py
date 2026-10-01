from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from app.db import Base
from app.core.config import settings

config = context.config
# Alembic runs inside the Uvicorn process during application startup. Keep
# Uvicorn and application loggers alive when loading Alembic's own formatter.
fileConfig(config.config_file_name, disable_existing_loggers=False)
config.set_main_option('sqlalchemy.url', settings['database']['url'])

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option('sqlalchemy.url')
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
