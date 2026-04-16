from __future__ import annotations

from app.common.db import RuntimeReporter


class ReportClient(RuntimeReporter):
    """Thin runtime-layer alias for shared event reporting."""

