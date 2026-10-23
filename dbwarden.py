"""dbwarden schema configuration.

The SQLAlchemy models in :mod:`app.models` are the schema contract. dbwarden
compiles versioned SQL migrations and rollbacks from them; there are no
hand-written Alembic revisions.
"""
from __future__ import annotations

import os

from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "sqlite"
    database_url_sync = os.getenv("DATABASE_URL", "sqlite:///./data/personal_api.db")
    model_paths = ["app/models.py"]
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./data/dev.db"
