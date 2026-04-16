from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, insert
from sqlalchemy.exc import SQLAlchemyError

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


metadata = MetaData()

runtime_events = Table(
    "task_runtime_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(128), nullable=False, index=True),
    Column("node_name", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("event_ts", DateTime(timezone=True), nullable=False),
    Column("frame_id", Integer, nullable=True),
    Column("latency_ms", Float, nullable=True),
    Column("payload", JSON, nullable=True),
)


class RuntimeReporter:
    def __init__(self, db_url: str, redis_url: str = "", redis_stream_key: str = "task_runtime_events"):
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        metadata.create_all(self.engine)
        self.redis_stream_key = redis_stream_key
        self.redis_client = None
        if redis_url and redis is not None:
            self.redis_client = redis.Redis.from_url(redis_url, decode_responses=True)

    def report(
        self,
        task_id: str,
        node_name: str,
        event_type: str,
        frame_id: int | None = None,
        latency_ms: float | None = None,
        payload: dict | None = None,
    ) -> None:
        event_payload = payload or {}
        stmt = insert(runtime_events).values(
            task_id=task_id,
            node_name=node_name,
            event_type=event_type,
            event_ts=datetime.now(timezone.utc),
            frame_id=frame_id,
            latency_ms=latency_ms,
            payload=event_payload,
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError:
            pass

        if self.redis_client is not None:
            try:
                self.redis_client.xadd(
                    self.redis_stream_key,
                    {
                        "task_id": task_id,
                        "node_name": node_name,
                        "event_type": event_type,
                        "event_ts": datetime.now(timezone.utc).isoformat(),
                        "frame_id": "" if frame_id is None else str(frame_id),
                        "latency_ms": "" if latency_ms is None else f"{latency_ms:.3f}",
                        "payload": str(event_payload),
                    },
                    maxlen=200000,
                    approximate=True,
                )
            except Exception:
                pass
