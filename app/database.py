from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # Import models so they are registered on Base before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# Columns added after the first release. create_all() never alters an
# existing table, so an upgraded install gets them here (SQLite can only
# ADD COLUMN, which is all we need).
_ADDED_COLUMNS = {
    "vpn_users": {
        "auth_mode": "VARCHAR(16) NOT NULL DEFAULT 'cert'",
        "auth_password": "VARCHAR(128)",
        "max_devices": "INTEGER NOT NULL DEFAULT 0",
        "tls_key": "TEXT",
        "tls_key_id": "VARCHAR(16)",
        "tls_key_seen_at": "DATETIME",
    },
    "relay_servers": {
        "source_ip": "VARCHAR(64)",
    },
}


def _add_missing_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
