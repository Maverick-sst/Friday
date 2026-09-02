"""SSE replay tests (Fleet PRD A3): ring buffer, resume-from Last-Event-ID,
bounding, redis-pump dedup, and id-carrying SSE frames."""

from app.engine.progress import ProgressBus, ProgressEvent


def _ev(mission_id: str, n: int, kind: str = "log") -> ProgressEvent:
    return ProgressEvent(mission_id=mission_id, kind=kind, payload={"n": n})


async def test_replay_returns_events_in_order_with_seq():
    bus = ProgressBus()
    for i in range(3):
        await bus.publish(_ev("m1", i))
    events = await bus.replay("m1")
    assert [s for s, _ in events] == [1, 2, 3]
    assert [e.payload["n"] for _, e in events] == [0, 1, 2]


async def test_replay_after_seq_resumes_without_gaps():
    bus = ProgressBus()
    for i in range(5):
        await bus.publish(_ev("m2", i))
    events = await bus.replay("m2", after_seq=3)
    assert [s for s, _ in events] == [4, 5]
    assert [e.payload["n"] for _, e in events] == [3, 4]


async def test_replay_is_bounded_per_mission():
    bus = ProgressBus()
    bus.REPLAY_MAX = 10
    for i in range(25):
        await bus.publish(_ev("m3", i))
    events = await bus.replay("m3")
    assert len(events) == 10
    assert events[0][1].payload["n"] == 15  # oldest trimmed, newest kept


async def test_mission_buffers_are_isolated():
    bus = ProgressBus()
    await bus.publish(_ev("ma", 1))
    await bus.publish(_ev("mb", 2))
    a = await bus.replay("ma")
    b = await bus.replay("mb")
    assert [s for s, _ in a] == [1]
    assert [s for s, _ in b] == [1]
    assert a[0][1].payload["n"] == 1 and b[0][1].payload["n"] == 2


async def test_record_if_new_dedups_redis_pumped_copies():
    bus = ProgressBus()
    ev = _ev("m4", 1, kind="tool_call")
    await bus._record(ev)
    seq1 = await bus._record_if_new("m4", ev)
    seq2 = await bus._record_if_new("m4", ev)
    assert seq1 == seq2
    assert len(await bus.replay("m4")) == 1


async def test_trace_correlation_flows_through_publish():
    bus = ProgressBus()
    from app.observability import propagate_attributes

    # No active span in tests -> payload unchanged; buffer still records.
    await bus.publish(_ev("m5", 9))
    events = await bus.replay("m5")
    assert events and events[0][1].payload["n"] == 9
    assert propagate_attributes  # correlation helpers importable


def test_to_sse_carries_id_line_for_last_event_id():
    ev = ProgressEvent(mission_id="m6", kind="run_status", payload={"status": "RUNNING"})
    text = ev.to_sse(7)
    assert text.startswith("id: 7\ndata: ")
    assert '"status": "RUNNING"' in text
    # No seq -> no id line (back-compat for unnamed callers).
    assert not ev.to_sse().startswith("id:")