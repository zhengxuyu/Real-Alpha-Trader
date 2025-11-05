from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# Single database: stores account metadata (LLM config) and AI decision logs
# All trading data (balances, positions, orders) is fetched in real-time from Binance
DATABASE_URL = "sqlite:///./metadata.db"

# Create engine with WAL mode for better concurrency
# WAL mode allows multiple readers and one writer simultaneously
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30.0,  # 30 second timeout for database operations
    },
    pool_pre_ping=True,  # Verify connections before using them
)

# Enable WAL mode for SQLite to improve concurrency
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode and other SQLite optimizations."""
    cursor = dbapi_conn.cursor()
    try:
        # Enable WAL mode (Write-Ahead Logging) for better concurrency
        cursor.execute("PRAGMA journal_mode=WAL")
        # Increase busy timeout to reduce lock errors
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 seconds
        # Optimize for performance
        cursor.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and speed
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass  # Ignore errors if PRAGMA commands fail
    finally:
        cursor.close()

# Create sessionmaker
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
