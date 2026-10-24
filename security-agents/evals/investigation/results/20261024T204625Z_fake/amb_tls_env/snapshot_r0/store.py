"""In-memory store for hooks-lite: webhook events, jobs and API sessions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    id: str
    source: str
    delivery_id: str
    payload: dict[str, object]


@dataclass
class Job:
    id: str
    kind: str
    status: str
    artifact_url: str | None = None


@dataclass
class ApiSession:
    token: str
    principal: str


@dataclass
class Store:
    events: dict[str, Event] = field(default_factory=dict)
    seen_deliveries: dict[str, float] = field(default_factory=dict)  # delivery_id -> epoch
    jobs: dict[str, Job] = field(default_factory=dict)
    sessions: dict[str, ApiSession] = field(default_factory=dict)
    _counter: int = 0

    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:04d}"


def seeded_store() -> Store:
    s = Store()
    s.jobs = {"job_a1": Job("job_a1", "report", "queued", "https://artifacts.example.test/a1.tgz")}
    s.sessions = {"fixture-token-ops": ApiSession("fixture-token-ops", "svc_ops")}
    return s
