"""In-process pub/sub so the demo UI can watch a run unfold (Server-Sent Events).

The crew flow runs in a background worker thread and calls `emit(...)` at each
milestone (and on each CrewAI step); the `/runs/{id}/events` endpoint polls
`snapshot()` and streams new events to the browser. Polling a shared buffer
(instead of bridging an asyncio queue across threads) keeps this dead-simple and
robust for a single-process demo app — no cross-thread event-loop coordination.

Events are plain dicts: {"seq", "ts", "type", ...fields}. The UI switches on
`type` (e.g. "run.started", "audience.planned", "channel.decision").
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional


class RunHub:
    """Thread-safe, bounded registry of in-flight/just-finished runs."""

    def __init__(self, max_runs: int = 32) -> None:
        self._runs: dict[str, dict] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._max_runs = max_runs

    def create(self, *, brief_id: str) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._runs[run_id] = {
                "brief_id": brief_id,
                "events": [],
                "done": False,
                "result": None,
                "error": None,
            }
            self._order.append(run_id)
            while len(self._order) > self._max_runs:  # evict the oldest run
                self._runs.pop(self._order.pop(0), None)
        return run_id

    def emit(self, run_id: str, type: str, **fields: Any) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["events"].append(
                {"seq": len(run["events"]), "ts": time.time(), "type": type, **fields}
            )

    def emitter(self, run_id: str) -> Callable[..., None]:
        """A bound `emit(type, **fields)` for the worker/flow to call."""

        def _emit(type: str, **fields: Any) -> None:
            self.emit(run_id, type, **fields)

        return _emit

    def finish(self, run_id: str, *, result: Optional[dict] = None, error: Optional[str] = None) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["result"] = result
            run["error"] = error
            run["done"] = True

    def snapshot(self, run_id: str, since: int) -> tuple[list[dict], bool, bool]:
        """Return (events with seq >= `since`, done, exists)."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return ([], True, False)
            return (run["events"][since:], run["done"], True)

    def status(self, run_id: str) -> Optional[dict]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return {
                "brief_id": run["brief_id"],
                "done": run["done"],
                "result": run["result"],
                "error": run["error"],
            }


# Process-wide hub (the buyer app is single-process for the demo).
hub = RunHub()
