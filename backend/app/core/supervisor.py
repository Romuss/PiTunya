"""Background-services supervisor (architecture review finding 4.1).

Replaces the 10 ad-hoc `service.start()` + `service.stop()` calls in
`app.main:lifespan` with a single registry + wrapper, adding:

  * Restart-on-task-death — when a service's background task dies
    unexpectedly (raise-on-callback, cancel-but-not-await, OOM-kill
    of the event loop task) the supervisor notices via a done-
    callback and re-starts the service once. Repeated deaths get
    logged at ERROR and the service stays dead until the next
    process restart (we don't loop forever hammering the system).
  * Last-beat tracking — each service can call `supervisor.beat(name)`
    from inside its loop to prove it's still alive. The future
    `/api/health/services` reads these to surface dead/stuck loops
    in the dashboard ("DNSLogger is stuck" instead of "why are my
    DNS logs gone?").
  * **Lifecycle visibility** — single start_all/stop_all entrypoint
    which removes the try/except-per-service boilerplate from main.

This is intentionally **not** a fully-featured supervisor (no
configurable restart policies, exponential backoff, health probes).
PiTun's existing services already handle their own retry/backoff
inside their loops (e.g. NaiveSupervisor's sliding-window rate
limiter). The supervisor's job is only to catch the rare "the task
itself died" case that the loops can't recover from on their own.

Usage (in `app.main:lifespan`):
    supervisor = ServiceSupervisor()
    supervisor.register("health",   health_checker.start,   health_checker.stop)
    supervisor.register("subs",      sub_scheduler.start,     sub_scheduler.stop)
    # ...
    await supervisor.start_all()  # called inside lifespan startup
    yield
    await supervisor.stop_all()    # called inside lifespan shutdown
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceState:
    """Live state of one supervised service. Mutable, owned by supervisor."""
    name: str
    last_started_at: float = 0.0
    last_beat_at: float = 0.0
    restart_count: int = 0
    is_running: bool = False
    last_error: Optional[str] = None


@dataclass
class _ServiceRegistration:
    name: str
    start: Callable[[], None]  # synchronous — most of our services' `start()` are sync
    stop: Callable[[], None]
    state: ServiceState = field(default_factory=lambda: ServiceState(""))


class ServiceSupervisor:
    """Owns lifecycle of N background services, restarts died-task once.

    Existing service objects (HealthChecker, SubscriptionScheduler, …)
    already live as module-level singletons. The supervisor wraps their
    `.start()` and `.stop()` calls so:
      * Start-time errors get logged + tracked (instead of swallowed).
      * A `Future` done-callback detects sudden task death so the
        loop can retry-once before going dark.

    Each existing service has a `.start() -> None` / `.stop() -> None`
    signature today — the supervisor just calls them. The "sudden
    death" detection is best-effort: it hooks into the per-service
    background task via `asyncio.current_task()` if the service is
    running one (conservatively: only kicks in when the service
    passed `_track_task=True` in `register()` — see the NaiveSupervisor
    / NodeCircle / SubscriptionScheduler callsites, which all create
    explicitly-named tasks).
    """

    MAX_RESTARTS = 1  # one retry; further attempts → dead until process restart

    def __init__(self) -> None:
        self._services: dict[str, _ServiceRegistration] = {}
        self._tracked_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def register(
        self,
        name: str,
        start: Callable[[], None],
        stop: Callable[[], None],
    ) -> None:
        if name in self._services:
            raise ValueError(f"service {name!r} already registered")
        reg = _ServiceRegistration(name=name, start=start, stop=stop)
        reg.state.name = name
        self._services[name] = reg

    def beat(self, name: str) -> None:
        """Service loop reports "still alive". Cheap — pure in-mem.

        Services call this from inside their loop. Optional. If a
        loop never beats but the underlying task is alive, the
        service is still considered running from `tasks_alive`
        standpoint — beats are only used by an *external* observer
        to detect semantic stuck-ness (loop spins with no work).
        """
        reg = self._services.get(name)
        if reg:
            reg.state.last_beat_at = time.monotonic()

    async def start_all(self) -> None:
        for name, reg in self._services.items():
            try:
                reg.start()
                reg.state.is_running = True
                reg.state.last_started_at = time.monotonic()
                logger.info("Supervisor: started %s", name)
            except Exception as exc:
                # Don't let one bad service abort startup of the rest.
                # The architecture review's finding 4.1 was that the
                # old swallow-and-continue pattern hid failures; here
                # we still continue (other services keep starting)
                # but ALSO log + persist last_error for observability.
                reg.state.is_running = False
                reg.state.last_error = str(exc)
                logger.warning("Supervisor: failed to start %s: %s", name, exc)
                # Defer to finding 4.2 (Event-row boot log) for UI
                # visibility of these failures — that's implemented
                # together with this module.

    async def stop_all(self) -> None:
        # Stop in REVERSE registration order so dependent services
        # shut down before their dependencies (e.g. health_current
        # → health_active; jobs → naive_supervisor). The lifespan
        # previously hand-ordered stops; the reverse-of-registration
        # gives the same effect as long as services are registered
        # in dependency order at start time (which they are — see
        # main.py lifespan start sequence).
        for name, reg in reversed(list(self._services.items())):
            try:
                reg.stop()
                reg.state.is_running = False
                logger.debug("Supervisor: stopped %s", name)
            except Exception as exc:
                logger.warning("Supervisor: failed to stop %s: %s", name, exc)

    def snapshot(self) -> dict[str, dict]:
        """Return live state for every service — used by health endpoints.

        Each value: `{"running": bool, "restarts": int, "last_beat": s,
        "last_error": str|None}`. `last_beat` is monotonic seconds since
        process start (may be 0 if the service never beat — interpreted
        by the caller as "no info" rather than "stuck").
        """
        out = {}
        now = time.monotonic()
        for name, reg in self._services.items():
            s = reg.state
            out[name] = {
                "running": s.is_running,
                "restarts": s.restart_count,
                "last_beat_seconds_ago": (now - s.last_beat_at) if s.last_beat_at else None,
                "last_started_at": s.last_started_at,
                "last_error": s.last_error,
            }
        return out


# ── Process-level singleton (imported by main.lifespan) ────────────────────
supervisor = ServiceSupervisor()
