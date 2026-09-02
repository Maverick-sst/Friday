"""Observability tests (OTEL_LANGFUSE_EXECUTION_PRD §34).

Tests 1-8 coverage via an injected fake tracing client (no Cloud access) plus
one real-wiring mission test. The conftest `_observability_off` autouse fixture
guarantees every OTHER test stays offline; these tests opt in by overriding
the module's client with a recording fake.
"""

import asyncio
import contextvars

import pytest

import app.observability as obs_mod
from app.observability import observation


# --- Recording fake client --------------------------------------------------


class _FakeObs:
    def __init__(self, recorder, name, as_type, **attrs):
        self.recorder = recorder
        self.span_id = f"span-{len(recorder.spans) + 1}"
        self.name = name
        self.as_type = as_type
        self.attrs = dict(attrs)
        self.parent = recorder.current
        self.status = None
        self.exc = None
        recorder.spans.append(self)

    def __enter__(self):
        self.recorder.current = self
        return self

    def __exit__(self, exc_type, exc, tb):
        self.recorder.current = self.parent
        self.exc = exc
        return False  # never swallow

    def update(self, **kw):
        self.attrs.update(kw)
        if "status" in kw:
            self.status = kw["status"]


class _FakeCurrentObservation:
    def __init__(self, client):
        self._client = client

    def get_trace_id(self, client=None):
        return self._client.trace_id

    def get_span_id(self, client=None):
        return self._client.current.span_id if self._client.current else None


class FakeLangfuse:
    """Recording fake that mimics Langfuse v4 OTel-native surface.

    Uses a contextvar for the active observation to mirror OTel's async
    context propagation, so concurrent tasks keep isolated parents.
    """

    def __init__(self, trace_id="trace-test"):
        self.spans: list[_FakeObs] = []
        self.trace_id = trace_id
        self.attrs: dict = {}
        self._current_var = contextvars.ContextVar("lf_fake_current", default=None)
        self.flush_called = False

    @property
    def current(self):
        return self._current_var.get()

    @current.setter
    def current(self, value):
        self._current_var.set(value)

    def start_as_current_observation(self, *, as_type, name, **attrs):
        return _FakeObs(self, name, as_type, **attrs)

    def propagate_attributes(self, **attrs):
        self.attrs.update(attrs)

    def flush(self):
        self.flush_called = True

    @property
    def current_observation(self):
        return _FakeCurrentObservation(self)


@pytest.fixture()
def fake_off():
    """Ensure a clean disabled state after each test."""
    yield
    obs_mod._override_client(None)


@pytest.fixture()
def fake_on():
    client = FakeLangfuse()
    obs_mod._override_client(client, enabled_flag=True)
    try:
        yield client
    finally:
        obs_mod._override_client(None)


# --- Tests ------------------------------------------------------------------


async def test_disabled_is_noop_and_business_continues(fake_off):
    """PRD 21 / test 8: Langfuse unavailable must not alter execution."""
    obs_mod._override_client(None)  # force disabled

    calls = []

    async def business():
        calls.append("start")
        with observation(name="llm.generate", as_type="generation") as span:
            assert span is None  # no-op identity manager
        calls.append("end")
        return "ok"

    result = await business()
    assert result == "ok"
    assert calls == ["start", "end"]


async def test_business_exception_propagates(fake_on):
    """PRD 21 / test 5: a business exception re-raises and marks span failed."""
    async def business():
        with observation(name="agent.market", as_type="agent") as _:
            raise LookupError("boom")

    with pytest.raises(LookupError, match="boom"):
        await business()

    assert len(fake_on.spans) == 1
    assert fake_on.spans[0].exc is not None  # marked as failed


async def test_nested_hierarchy(fake_on):
    """PRD 9.2 / test 2: nested observations keep parent-child shape."""
    async def outer():
        with observation(name="mission.baseline", as_type="span") as mission_span:
            self_ = mission_span
            with observation(name="agent.market", as_type="agent"):
                with observation(name="tool.web_search", as_type="tool"):
                    pass
            return self_

    mission_span = await outer()

    agent_span = next(s for s in fake_on.spans if s.name == "agent.market")
    tool_span = next(s for s in fake_on.spans if s.name == "tool.web_search")
    assert agent_span.parent is mission_span
    assert tool_span.parent is agent_span


async def test_generation_observation_shape(fake_on):
    """PRD 9.3 / test 3: generations capture model + status."""
    async def gen():
        with observation(name="llm.generate", as_type="generation", model="m-a") as g:
            g.update(status="COMPLETED", output="done")

    await gen()
    span = fake_on.spans[0]
    assert span.as_type == "generation"
    assert span.attrs["model"] == "m-a"
    assert span.status == "COMPLETED"


async def test_tool_observation_shape(fake_on):
    """PRD 9.4 / test 4: tool observations capture name + status."""
    async def tool():
        with observation(name="tool.search_shopping", as_type="tool", input="running shoes") as t:
            t.update(status="ok", result_count=3)

    await tool()
    span = fake_on.spans[0]
    assert span.as_type == "tool"
    assert span.attrs["input"] == "running shoes"
    assert span.status == "ok"


async def test_concurrent_isolation(fake_on):
    """PRD 10 / test 6: concurrent chains keep isolated trace context."""
    async def chain(label: str):
        with observation(name=f"chain.{label}", as_type="span"):
            with observation(name=f"agent.{label}", as_type="agent"):
                return fake_on.current.span_id

    await asyncio.gather(chain("a"), chain("b"))

    a_chain = next(s for s in fake_on.spans if s.name == "chain.a")
    b_chain = next(s for s in fake_on.spans if s.name == "chain.b")
    a_agent = next(s for s in fake_on.spans if s.name == "agent.a")
    b_agent = next(s for s in fake_on.spans if s.name == "agent.b")

    assert a_agent.parent is a_chain  # mission A stays under A
    assert b_agent.parent is b_chain  # mission B under B
    assert a_agent not in {b_chain, b_agent}


async def test_flush_called(fake_on):
    """PRD 22/40: flush_telemetry() flushes the client."""
    obs_mod.flush_telemetry()
    assert fake_on.flush_called is True


# --- Integration: real mission emits mission + agent spans (test 1) ---------


async def test_mission_execution_emits_mission_and_agent_spans(
    async_db, merchant_row, session_factory, monkeypatch
):
    """Run a real mission with the fake client; assert mission + agent spans."""
    from app.engine.executor import execute_mission
    from app.engine.queue import InProcessJobQueue
    from app.intel import handlers as intel_handlers
    from app.intel.handlers import register_all as _register_intel_handlers
    from app.intel.schemas import ResearchOutput
    from app.llm.provider import LLMResponse

    _register_intel_handlers()

    class _LLM:
        async def structured_generate(self, messages, schema, **kw):
            out = ResearchOutput(
                summary="Research finished.",
                claims=[],
                findings=[],
                confidence=0.7,
            )
            return out, LLMResponse(text="", model_used="fake")

        async def generate(self, messages, **kw):
            return LLMResponse(text="ok", model_used="fake")

    class _Mem:
        async def add(self, merchant_id, text, *, kind="observation", mission_id=None, metadata=None):
            return None

        async def search(self, merchant_id, query, *, k=5):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(intel_handlers, "_get_llm", lambda: _LLM())
    monkeypatch.setattr(intel_handlers, "_get_memory", lambda: _Mem())
    monkeypatch.setattr(
        intel_handlers,
        "_get_plane",
        lambda: __import__("app.tools.mock_plane", fromlist=["MockToolPlane"]).MockToolPlane(),
    )
    monkeypatch.setattr(intel_handlers, "_session_factory", lambda: session_factory())

    fake = FakeLangfuse(trace_id="trace-mission-1")
    obs_mod._override_client(fake, enabled_flag=True)
    try:
        from app.db.models import Mission

        mission = Mission(
            merchant_id=merchant_row.id,
            name="Obs test",
            objective="Run a quick market scan.",
            mission_type="on_demand",
            status="QUEUED",
            budget_runs=5,
            max_runtime_seconds=120,
            agent_assignments_json=["market"],
        )
        async_db.add(mission)
        await async_db.commit()

        queue = InProcessJobQueue()
        try:
            status = await execute_mission(mission.id, queue, "w1", session_factory=session_factory)
        finally:
            await queue.close()

        assert status == "COMPLETED"
        names = [s.name for s in fake.spans]
        assert any(n.startswith("mission.") for n in names), names
        assert "agent.market" in names, names
        mission_span = next(s for s in fake.spans if s.name.startswith("mission."))
        agent_span = next(s for s in fake.spans if s.name == "agent.market")
        assert agent_span.parent is mission_span
        assert fake.attrs.get("mission_id") == mission.id
    finally:
        obs_mod._override_client(None)