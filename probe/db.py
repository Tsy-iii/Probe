from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str, *, schema: str | None = None):
        self.schema = schema
        if make_url(url).drivername != "postgresql+psycopg":
            raise ValueError("Probe requires postgresql+psycopg; SQLite is not supported")
        options = "-c statement_timeout=5000 -c lock_timeout=3000"
        if schema is not None:
            # schema names come only from the isolated test fixture, never HTTP input.
            options += f" -c search_path={schema}"
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            connect_args={"connect_timeout": 3, "options": options},
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def close(self):
        self.engine.dispose()
