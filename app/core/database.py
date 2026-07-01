from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from app.core.config import get_settings
from app.core.security import hash_password

settings = get_settings()
connect_args = {'check_same_thread': False} if settings.database_url.startswith('sqlite') else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class EventLog(Base):
    __tablename__ = 'event_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), default='system', nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskLog(Base):
    __tablename__ = 'task_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actor: Mapped[str] = mapped_column(String(128), default='system', nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='running', nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserAccount(Base):
    __tablename__ = 'user_account'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default='operator', nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_default_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.query(UserAccount).count()
        if existing == 0:
            admin = UserAccount(
                username=settings.username,
                password_hash=hash_password(settings.password),
                role='admin',
                is_active=True,
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    seed_default_admin()
