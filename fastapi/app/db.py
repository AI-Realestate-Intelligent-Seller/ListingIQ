from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .core.config import settings

DATABASE_URL = settings['database']['url']

connect_args = {}
if DATABASE_URL.startswith('sqlite'):
    connect_args['check_same_thread'] = False

engine = create_engine(DATABASE_URL, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# Registers the session events that invalidate cached API payloads on commit.
# Imported here so every process that writes through SessionLocal (API, workers,
# scripts) keeps the cache honest, not only the web app.
from . import cache  # noqa: E402,F401

def init_db():
    from . import models
    Base.metadata.create_all(bind=engine)
