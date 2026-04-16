from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


class RuntimeReporter:
    def __init__(self, db_url: str, redis_url: str = "", redis_stream_key: str = "task_runtime_events"):
        self.engine = None
        if db_url and str(db_url).strip():
            try:
                self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
            except Exception:
                self.engine = None
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
        now_dt = datetime.now(timezone.utc)
        if self.engine is not None:
            try:
                self._upsert_task_status_mysql(task_id=task_id, event_type=event_type, now_dt=now_dt)
                if event_type == "ui_metrics" and isinstance(event_payload, dict):
                    self._upsert_task_metrics_mysql(task_id=task_id, event_payload=event_payload, now_dt=now_dt)
            except Exception:
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

    def _upsert_task_status_mysql(self, *, task_id: str, event_type: str, now_dt: datetime) -> None:
        if self.engine is None:
            return
        with self.engine.begin() as c:
            sql = text(
                """
                INSERT INTO task_runtime_status (
                    task_id, task_type, state, started_at, ended_at, last_update_at
                ) VALUES (
                    :task_id, 'video_infer', 'running', :now_dt, NULL, :now_dt
                )
                ON DUPLICATE KEY UPDATE
                    last_update_at = VALUES(last_update_at),
                    state = :next_state,
                    ended_at = CASE
                        WHEN :is_stop = 1 THEN :now_dt
                        ELSE ended_at
                    END
                """
            )
            c.execute(
                sql,
                {
                    "task_id": task_id,
                    "now_dt": now_dt,
                    "next_state": "ended" if event_type in {"sender_stop", "receiver_stop"} else "running",
                    "is_stop": 1 if event_type in {"sender_stop", "receiver_stop"} else 0,
                },
            )

    def _upsert_task_metrics_mysql(self, *, task_id: str, event_payload: dict[str, Any], now_dt: datetime) -> None:
        if self.engine is None:
            return
        role = str(event_payload.get("role") or "unknown")
        sample_count = int(event_payload.get("count") or event_payload.get("sample_count") or 0)
        meet_cnt = event_payload.get("rtt_meet_target") or event_payload.get("meet_target_count")
        meet_ratio = event_payload.get("rtt_meet_ratio") or event_payload.get("meet_target_ratio")
        target_latency = None
        prof = event_payload.get("profile") if isinstance(event_payload.get("profile"), dict) else {}
        if isinstance(prof, dict):
            target_latency = prof.get("target_latency_ms") or event_payload.get("target_latency_ms")
        with self.engine.begin() as c:
            sql = text(
                """
                INSERT INTO task_runtime_metrics (
                    task_id, role, sample_count, infer_avg_ms, infer_p95_ms,
                    rtt_avg_ms, rtt_p95_ms, latest_rtt_ms, meet_target_count,
                    meet_target_ratio, target_latency_ms, updated_at
                ) VALUES (
                    :task_id, :role, :sample_count, :infer_avg_ms, :infer_p95_ms,
                    :rtt_avg_ms, :rtt_p95_ms, :latest_rtt_ms, :meet_target_count,
                    :meet_target_ratio, :target_latency_ms, :updated_at
                )
                ON DUPLICATE KEY UPDATE
                    sample_count = VALUES(sample_count),
                    infer_avg_ms = VALUES(infer_avg_ms),
                    infer_p95_ms = VALUES(infer_p95_ms),
                    rtt_avg_ms = VALUES(rtt_avg_ms),
                    rtt_p95_ms = VALUES(rtt_p95_ms),
                    latest_rtt_ms = VALUES(latest_rtt_ms),
                    meet_target_count = VALUES(meet_target_count),
                    meet_target_ratio = VALUES(meet_target_ratio),
                    target_latency_ms = VALUES(target_latency_ms),
                    updated_at = VALUES(updated_at)
                """
            )
            c.execute(
                sql,
                {
                    "task_id": task_id,
                    "role": role,
                    "sample_count": sample_count,
                    "infer_avg_ms": event_payload.get("infer_avg_ms"),
                    "infer_p95_ms": event_payload.get("infer_p95_ms"),
                    "rtt_avg_ms": event_payload.get("rtt_avg_ms"),
                    "rtt_p95_ms": event_payload.get("rtt_p95_ms"),
                    "latest_rtt_ms": event_payload.get("latest_rtt_ms"),
                    "meet_target_count": None if meet_cnt is None else int(meet_cnt),
                    "meet_target_ratio": None if meet_ratio is None else float(meet_ratio),
                    "target_latency_ms": None if target_latency is None else float(target_latency),
                    "updated_at": now_dt,
                },
            )
