from probe.db import Database
from probe.platform.config import PlatformSettings
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from probe.platform.config import ROOT


def migrate(database):
    configuration = Config(str(ROOT / "alembic.ini"))
    with database.engine.connect() as connection:
        configuration.attributes["connection"] = connection
        inspector = inspect(connection)
        if inspector.has_table("test_runs") and not inspector.has_table("alembic_version"):
            columns = {column["name"] for column in inspector.get_columns("test_runs")}
            if "attempt_count" in columns:
                raise RuntimeError("Unversioned stage-3 schema detected; review it before migration")
            command.stamp(configuration, "0001_stage2")
        command.upgrade(configuration, "head")
        connection.commit()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Initialize the separate platform schema without resetting data")
    parser.add_argument("command", choices=["init-db"])
    parser.parse_args()
    settings = PlatformSettings()
    database = Database(settings.database_url, schema=settings.database_schema)
    try:
        migrate(database)
        print("Platform migrations applied; existing runs and reports preserved.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
