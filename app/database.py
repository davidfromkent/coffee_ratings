import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use Render / local environment variable if set, otherwise fall back to local SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///coffee.db")

engine_kwargs = {"pool_pre_ping": True}

# SQLite needs this for FastAPI usage
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

