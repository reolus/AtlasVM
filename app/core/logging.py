from sqlalchemy.orm import Session
from app.core.database import EventLog


def log_event(db: Session, actor: str, action: str, target: str | None = None, message: str | None = None) -> EventLog:
    event = EventLog(actor=actor, action=action, target=target, message=message)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
