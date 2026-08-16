from alembic import context
from sqlalchemy import create_engine

from app.config import DATABASE_URL

config = context.config


def run_migrations_online():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
