from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from app.config import Settings, get_settings
from app.db import Database
from app.services.sync import (
    claim_job,
    execute_job,
    schedule_recurring,
)
from app.utils import utcnow

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or get_settings()
        self.db = database or Database(self.settings.database_url)
        self._stop = threading.Event()
        self._next_schedule = utcnow()

    def run_once(self) -> bool:
        with self.db.session() as session:
            try:
                if utcnow() >= self._next_schedule:
                    scheduled = schedule_recurring(session, self.settings)
                    if scheduled:
                        log.info("scheduled %s recurring refresh jobs", scheduled)
                    self._next_schedule = utcnow() + timedelta(minutes=5)
                    session.commit()
                job = claim_job(session)
                if job is None:
                    session.commit()
                    return False
                session.commit()
                log.info("running job %s scope=%s repository=%s", job.id, job.scope, job.repository_id)
                result = execute_job(session, self.settings, job)
                session.commit()
                log.info("job %s finished: %s", job.id, result.get("status"))
                return True
            except Exception:
                session.rollback()
                log.exception("worker iteration failed")
                return False

    def run(self, poll_seconds: float | None = None) -> None:
        poll = poll_seconds if poll_seconds is not None else self.settings.worker_poll_seconds
        while not self._stop.is_set():
            worked = self.run_once()
            if not worked:
                self._stop.wait(poll)

    def stop(self) -> None:
        self._stop.set()


def run_workers(concurrency: int = 1, once: bool = False) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    if once:
        Worker(settings, db).run_once()
        return
    workers = [Worker(settings, db) for _ in range(max(1, concurrency))]
    threads = [threading.Thread(target=worker.run, daemon=True) for worker in workers]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        for worker in workers:
            worker.stop()
        for thread in threads:
            thread.join(timeout=10)
