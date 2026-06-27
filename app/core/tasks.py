from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.core.database import TaskLog


def start_task(db: Session, actor: str, action: str, target: str | None = None, message: str | None = None) -> TaskLog:
    task = TaskLog(actor=actor, action=action, target=target, status='running', message=message)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def finish_task(db: Session, task: TaskLog, status: str = 'success', message: str | None = None) -> TaskLog:
    task.status = status
    task.finished_at = datetime.now(timezone.utc)
    if message is not None:
        task.message = message
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
