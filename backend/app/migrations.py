from sqlalchemy import inspect, text

from .database import Base, engine


VEHICLE_COLUMNS = {
    "last_live_bid": "NUMERIC(14,2)",
    "last_live_bid_at": "TIMESTAMP WITH TIME ZONE",
    "finished_at": "TIMESTAMP WITH TIME ZONE",
    "monitoring_gap_seconds": "INTEGER",
    "price_data_valid": "BOOLEAN DEFAULT FALSE",
    "price_source": "VARCHAR(80)",
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
    first_quality_migration = "price_data_valid" not in existing["vehicles"]
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
        if first_quality_migration:
            connection.execute(text("UPDATE vehicles SET price_data_valid = FALSE"))
            connection.execute(text("UPDATE vehicles SET status = 'historical_unreliable', price_source = 'legacy_5_minute_polling' WHERE status IN ('closed', 'finalizing', 'verified', 'verification_failed')"))
            connection.execute(text("UPDATE auction_results SET final_price_status = 'historical_unreliable' WHERE final_price_status IN ('finalizing', 'verified') OR final_price_status IS NULL"))
        connection.execute(text("UPDATE auction_results SET final_price_status = 'finalizing' WHERE final_price_status IS NULL"))
