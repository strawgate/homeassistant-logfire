"""Tests for bounded, failure-isolated delivery."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.logfire.core.delivery import DeliveryQueue, DeliveryStatus
from custom_components.logfire.core.model import TelemetryRecord


def _record(event_name: str) -> TelemetryRecord:
    return TelemetryRecord(
        event_name=event_name,
        body=event_name,
        attributes={"event.name": event_name},
        timestamp_ns=1,
    )


class RecordingEmitter:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.attempts: list[str] = []
        self.fail_first = fail_first

    def emit(self, record: TelemetryRecord) -> None:
        self.attempts.append(record.event_name)
        if self.fail_first and len(self.attempts) == 1:
            raise RuntimeError("simulated exporter failure")


def test_full_queue_drops_new_record_without_blocking() -> None:
    outcomes = []
    queue = DeliveryQueue(RecordingEmitter(), capacity=1, on_outcome=outcomes.append)

    assert queue.enqueue(_record("first"))
    assert not queue.enqueue(_record("second"))

    assert queue.size == 1
    assert queue.capacity == 1
    assert queue.stats.enqueued == 1
    assert queue.stats.dropped == 1
    assert [outcome.status for outcome in outcomes] == [
        DeliveryStatus.ENQUEUED,
        DeliveryStatus.DROPPED,
    ]
    assert outcomes[-1].reason == "application_queue_full"


def test_delivery_observer_failure_cannot_break_producer(caplog: pytest.LogCaptureFixture) -> None:
    def failing_observer(outcome) -> None:
        raise RuntimeError("simulated observer failure")

    queue = DeliveryQueue(RecordingEmitter(), capacity=1, on_outcome=failing_observer)

    assert queue.enqueue(_record("still-enqueued"))
    assert queue.stats.enqueued == 1
    assert "Delivery observer failed" in caplog.text


async def test_emit_failure_does_not_kill_worker() -> None:
    emitter = RecordingEmitter(fail_first=True)
    outcomes = []
    queue = DeliveryQueue(emitter, capacity=2, on_outcome=outcomes.append)
    worker = asyncio.create_task(queue.run())

    queue.enqueue(_record("first"))
    queue.enqueue(_record("second"))
    assert await queue.drain(0.5)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    assert emitter.attempts == ["first", "second"]
    assert queue.stats.failed == 1
    assert queue.stats.emitted == 1
    assert queue.stats.last_error_at is not None
    assert queue.stats.last_emit_at is not None
    assert [outcome.status for outcome in outcomes] == [
        DeliveryStatus.ENQUEUED,
        DeliveryStatus.ENQUEUED,
        DeliveryStatus.FAILED,
        DeliveryStatus.EMITTED,
    ]


async def test_drain_is_bounded_when_no_worker_is_running() -> None:
    queue = DeliveryQueue(RecordingEmitter(), capacity=1)
    queue.enqueue(_record("queued"))

    assert not await queue.drain(0.001)
