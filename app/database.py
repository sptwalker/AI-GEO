"""数据库连接与会话。

SQLAlchemy 2.0 同步会话，简单稳定，避免异步 ORM 的坑。网络密集的模型调用
在 services 层用 httpx 异步并发处理，DB 侧保持同步即可。
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# pool_pre_ping：连接失效自动重连，防止 MySQL 空闲断连导致偶发报错。
# sqlite（本地/测试）需要放开跨线程限制。
if settings.database_url.startswith("sqlite"):
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}, future=True
    )

    @event.listens_for(engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _rec):  # noqa: ANN001
        # sqlite 默认不强制外键；开启后 ON DELETE CASCADE 才生效（与 MySQL 一致）
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=3600, future=True
    )
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供请求级数据库会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
