"""Framework-independent bounded, intentionally lossy record delivery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .model import TelemetryRecord

_LOGGER = logging.getLogger(__name__)


class RecordEmitter(Protocol):
    """Synchronous sink used by the OpenTelemetry client."""

    def emit(self, record: TelemetryRecord) -> None:
        """Accept one record without blocking the event producer."""


class DeliveryStatus(StrEnum):
    """Observable delivery transitions."""

    ENQUEUED = "enqueued"
    DROPPED = "dropped"
    EMITTED = "emitted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """One delivery transition for adapter-owned counters and logging."""

    status: DeliveryStatus
    record: TelemetryRecord
    reason: str | None = None
    error: Exception | None = None


@dataclass(slots=True)
class DeliveryStats:
    """Non-sensitive queue state suitable for diagnostics."""

    dropped: int = 0
    emitted: int = 0
    enqueued: int = 0
    failed: int = 0
    last_error_at: str | None = None
    last_emit_at: str | None = None


class DeliveryQueue:
    """Decouple event callbacks from synchronous SDK emission."""

    def __init__(
        self,
        emitter: RecordEmitter,
        capacity: int,
        on_outcome: Callable[[DeliveryOutcome], None] | None = None,
    ) -> None:
        """Create a bounded queue; new records are dropped when it is full."""
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._emitter = emitter
        self._queue: asyncio.Queue[TelemetryRecord] = asyncio.Queue(capacity)
        self._on_outcome = on_outcome
        self.stats = DeliveryStats()

    @property
    def capacity(self) -> int:
        """Return the configured queue capacity."""
        return self._queue.maxsize

    @property
    def size(self) -> int:
        """Return the current number of queued records."""
        return self._queue.qsize()

    def enqueue(self, record: TelemetryRecord) -> bool:
        """Enqueue immediately, returning false when loss is required."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.stats.dropped += 1
            self._notify(
                DeliveryOutcome(
                    DeliveryStatus.DROPPED,
                    record,
                    reason="application_queue_full",
                )
            )
            return False
        self.stats.enqueued += 1
        self._notify(DeliveryOutcome(DeliveryStatus.ENQUEUED, record))
        return True

    async def run(self) -> None:
        """Drain records forever until the owning adapter cancels this task."""
        while True:
            record = await self._queue.get()
            try:
                self._emitter.emit(record)
            except Exception as error:  # telemetry failures must not kill the worker
                self.stats.failed += 1
                self.stats.last_error_at = datetime.now().astimezone().isoformat()
                self._notify(
                    DeliveryOutcome(
                        DeliveryStatus.FAILED,
                        record,
                        reason="sdk_emit_error",
                        error=error,
                    )
                )
            else:
                self.stats.emitted += 1
                self.stats.last_emit_at = datetime.now().astimezone().isoformat()
                self._notify(DeliveryOutcome(DeliveryStatus.EMITTED, record))
            finally:
                self._queue.task_done()

    async def drain(self, timeout_seconds: float) -> bool:
        """Wait a bounded time for queued work, returning whether the queue drained."""
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._queue.join()
        except TimeoutError:
            return False
        return True

    def _notify(self, outcome: DeliveryOutcome) -> None:
        if self._on_outcome is None:
            return
        try:
            self._on_outcome(outcome)
        except Exception:
            _LOGGER.exception("Delivery observer failed")
