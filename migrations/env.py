from alembic import context

from probe.db import Database
from probe.platform.config import PlatformSettings
from probe.platform.models import PlatformBase


def migrate(connection):
    context.configure(connection=connection, target_metadata=PlatformBase.metadata)
    with context.begin_transaction():
        context.run_migrations()


supplied = context.config.attributes.get("connection")
if supplied is not None:
    migrate(supplied)
else:
    settings = PlatformSettings()
    database = Database(settings.database_url, schema=settings.database_schema)
    try:
        with database.engine.connect() as connection:
            migrate(connection)
    finally:
        database.close()
