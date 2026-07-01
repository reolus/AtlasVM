from __future__ import annotations

import traceback
from threading import Thread
from typing import Callable, Any

from app.core.database import SessionLocal, TaskLog
from app.core.logging import log_event


def enqueue_task(actor: str, action: str, target: str | None, worker: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
    """Create a queued TaskLog row and run worker in a daemon thread.

    This is intentionally simple for Phase 5: single-node, in-process jobs.
    Long operations no longer block the browser request, and task status is visible
    in the Tasks page. A later phase can replace this with a durable external worker.
    """
    db = SessionLocal()
    try:
        task = TaskLog(actor=actor, action=action, target=target, status='queued', message='Queued')
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id
    finally:
        db.close()

    thread = Thread(
        target=_run_task,
        args=(task_id, actor, action, target, worker, args, kwargs),
        daemon=True,
    )
    thread.start()
    return task_id


def _run_task(task_id: int, actor: str, action: str, target: str | None, worker: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task is None:
            return
        task.status = 'running'
        task.message = 'Running'
        db.commit()

        result = worker(*args, **kwargs)
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task is None:
            return
        task.status = 'success'
        task.message = str(result) if result is not None else 'Completed'
        from datetime import datetime, timezone
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        log_event(db, actor, action, target, task.message)
    except Exception as exc:
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task is not None:
            task.status = 'failed'
            task.message = str(exc) + '\n' + traceback.format_exc()
            from datetime import datetime, timezone
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
        try:
            log_event(db, actor, f'{action}_failed', target, str(exc))
        except Exception:
            pass
    finally:
        db.close()
