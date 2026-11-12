"""Alembic environment. URL lấy từ config.attributes['sqlalchemy_url'] hoặc DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from trucca.db import metadata

config = context.config
url = config.attributes.get("sqlalchemy_url") or os.environ["DATABASE_URL"]
if url.startswith("postgresql://"):
    url = "postgresql+psycopg://" + url[len("postgresql://"):]


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            # Tránh 2 instance cùng migrate một lúc
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(724110001)")
            context.run_migrations()
    engine.dispose()


run_migrations_online()
