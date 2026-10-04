"""SpendWise AI API main application."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.db.dependencies import get_db

app = FastAPI(title="SpendWise AI API")

app.include_router(auth_router)


@app.get("/")
def read_root():
    return {"message": "SpendWise AI API is running"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}
