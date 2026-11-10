"""Explicit schema initialization and idempotent demo seeding; never on startup."""
import argparse
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url

from probe.config import Settings
from probe.db import Base, Database
from probe.models import Order, PaymentEvent, Product


DEMO_PRODUCTS = [
    {"id": 1, "name": "Probe Notebook", "price_cents": 1990, "stock": 10},
    {"id": 2, "name": "Probe Pen", "price_cents": 500, "stock": 3},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init-db", "migrate-db", "seed", "reset-demo"])
    parser.add_argument("--confirm-reset-demo", action="store_true", help="Delete orders/events for demo products 1 and 2")
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.database_url)
    db = Database(settings.database_url)
    try:
        if args.command == "init-db":
            Base.metadata.create_all(db.engine)
            with db.engine.begin() as connection:
                connection.execute(text((Path(__file__).resolve().parents[1] / "migrations" / "order_0001_product_active.sql").read_text()))
            print("Schema initialized; existing tables are preserved.")
            return
        if args.command == "migrate-db":
            if url.database != "probe":
                raise SystemExit("Order migration only targets the Probe demo database probe")
            with db.engine.begin() as connection:
                connection.execute(text((Path(__file__).resolve().parents[1] / "migrations" / "order_0001_product_active.sql").read_text()))
            print("Order migration applied; existing products, orders and events preserved.")
            return
        with db.sessions.begin() as session:
            if args.command == "reset-demo":
                if settings.app_env != "development" or url.database != "probe" or not args.confirm_reset_demo:
                    raise SystemExit("reset-demo requires APP_ENV=development, database probe and --confirm-reset-demo; stop the API first")
                # Only the seeded demo products are reset, in one transaction.
                # Stop the local API before running this maintenance command.
                session.execute(select(Product).where(Product.id.in_([1, 2])).with_for_update()).all()
                demo_order_ids = select(Order.id).where(Order.product_id.in_([1, 2]))
                session.execute(delete(PaymentEvent).where(PaymentEvent.order_id.in_(demo_order_ids)))
                session.execute(delete(Order).where(Order.product_id.in_([1, 2])))
                for row in DEMO_PRODUCTS:
                    statement = insert(Product).values(**row)
                    session.execute(statement.on_conflict_do_update(
                        index_elements=[Product.id],
                        set_={"name": row["name"], "price_cents": row["price_cents"], "stock": row["stock"], "is_active": True},
                    ))
            else:
                for row in DEMO_PRODUCTS:
                    session.execute(insert(Product).values(**row).on_conflict_do_nothing(index_elements=[Product.id]))
        print("Demo products ready: product 1 = 1990 cents / 10 initial stock; product 2 = 500 cents / 3 initial stock.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
