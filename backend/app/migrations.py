from sqlalchemy import inspect, text

from .database import Base, engine


VEHICLE_COLUMNS = {
    "last_live_bid": "NUMERIC(14,2)",
    "finished_at": "TIMESTAMP WITH TIME ZONE",
    "next_poll_at": "TIMESTAMP WITH TIME ZONE",
}
RESULT_COLUMNS = {
    "verified_final_price": "NUMERIC(14,2)",
    "final_price_verified_at": "TIMESTAMP WITH TIME ZONE",
    "final_price_source": "TEXT",
    "final_price_status": "VARCHAR(30) DEFAULT 'finalizing'",
    "verification_attempts": "INTEGER DEFAULT 0",
}


def migrate():
    """Small, idempotent migration for the existing production database."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    existing = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("vehicles", "auction_results")
    }
    with engine.begin() as connection:
        for name, ddl in VEHICLE_COLUMNS.items():
            if name not in existing["vehicles"]:
                connection.execute(text(f"ALTER TABLE vehicles ADD COLUMN {name} {ddl}"))
        for name, ddl in RESULT_COLUMNS.items():
            if name not in existing["auction_results"]:
                connection.execute(text(f"ALTER TABLE auction_results ADD COLUMN {name} {ddl}"))
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE auction_results ALTER COLUMN final_bid DROP NOT NULL"))
        connection.execute(text("UPDATE vehicles SET last_live_bid = current_bid WHERE last_live_bid IS NULL"))
        connection.execute(text("UPDATE vehicles SET status = 'finalizing' WHERE status = 'closed'"))
        if engine.dialect.name == "postgresql":
            connection.execute(text("UPDATE vehicles SET status = 'active', next_poll_at = NULL, finished_at = NULL WHERE status = 'finalizing' AND auction_end_time > NOW()"))
            connection.execute(text("UPDATE auction_results ar SET final_bid = NULL, verified_final_price = NULL, final_price_verified_at = NULL, final_price_source = NULL, final_price_status = 'live' FROM vehicles v WHERE ar.vehicle_id = v.id AND v.status = 'active' AND v.auction_end_time > NOW() AND ar.final_price_status = 'finalizing'"))
        connection.execute(text("UPDATE auction_results SET final_price_status = 'finalizing' WHERE final_price_status IS NULL"))
