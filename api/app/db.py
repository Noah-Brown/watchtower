from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def get_db():
    # Endpoints call db.commit() themselves before returning; committing in
    # teardown would run after the response is sent, letting a client that got
    # a 200 read stale state on an immediate follow-up request.
    with Session(engine) as session:
        yield session
        session.commit()  # backstop for read-only paths / anything missed
