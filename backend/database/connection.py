from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Single database: stores account metadata (LLM config) and AI decision logs
# All trading data (balances, positions, orders) is fetched in real-time from Binance
DATABASE_URL = "sqlite:///./metadata.db"

# Create engine
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

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
