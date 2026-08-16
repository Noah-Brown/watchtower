"""Canonical event envelope and per-type payload schemas.

Unknown payload keys are dropped, not stored (extra="ignore"). The envelope is
the only thing adapters may send; anything that doesn't validate is rejected
with a 422 and never enters the system.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Harness = Literal["claude-code", "codex", "antigravity", "opencode", "gemini", "other"]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessionStart(_Payload):
    cwd: str | None = None
    branch: str | None = None
    model: str | None = None
    parent_session_id: str | None = None


class SessionEnd(_Payload):
    reason: Literal["completed", "error", "killed", "timeout"] = "completed"
    summary: str | None = None


class Heartbeat(_Payload):
    status: Literal["working", "idle", "blocked"] = "working"


class Activity(_Payload):
    phase: Literal["planning", "editing", "running", "testing", "reviewing", "waiting"]
    label: str | None = None


class ToolCall(_Payload):
    tool: str
    ok: bool = True
    duration_ms: int | None = None


class Usage(_Payload):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    model: str | None = None


class NeedsInput(_Payload):
    kind: Literal["permission", "question", "approval"]
    prompt: str
    options: list[str] | None = None
    decision_id: str | None = None


class DecisionRequest(_Payload):
    title: str
    context: str | None = None
    options: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    urgency: Literal["low", "normal", "high"] = "normal"


class DeployRequest(_Payload):
    app_slug: str
    env: str
    ref: str
    summary: str | None = None
    checks: list[str] = Field(default_factory=list)


class Artifact(_Payload):
    kind: Literal["pr", "commit", "doc", "url"]
    ref: str
    title: str | None = None


class Log(_Payload):
    level: Literal["debug", "info", "warn", "error"] = "info"
    message: str


PAYLOAD_SCHEMAS: dict[str, type[_Payload]] = {
    "session.start": SessionStart,
    "session.end": SessionEnd,
    "session.heartbeat": Heartbeat,
    "activity": Activity,
    "tool.call": ToolCall,
    "usage": Usage,
    "needs_input": NeedsInput,
    "decision.request": DecisionRequest,
    "deploy.request": DeployRequest,
    "artifact": Artifact,
    "log": Log,
}

EventType = Literal[
    "session.start",
    "session.end",
    "session.heartbeat",
    "activity",
    "tool.call",
    "usage",
    "needs_input",
    "decision.request",
    "deploy.request",
    "artifact",
    "log",
]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    ts: datetime
    harness: Harness
    harness_version: str | None = None
    session_id: str
    project_slug: str | None = None
    host: str | None = None
    type: EventType
    seq: int
    payload: dict = Field(default_factory=dict)

    def validated_payload(self) -> dict:
        schema = PAYLOAD_SCHEMAS[self.type]
        return schema.model_validate(self.payload).model_dump(exclude_none=True)


class DecisionAnswer(BaseModel):
    answer: str


class DeploymentCreate(BaseModel):
    app_slug: str
    env: str = "prod"
    ref: str
    summary: str | None = None
    checks: list[str] = Field(default_factory=list)
    session_id: str | None = None
    project_slug: str | None = None


class DeploymentVerdict(BaseModel):
    notes: str | None = None


class DecisionCreate(BaseModel):
    project_slug: str | None = None
    session_id: str | None = None
    kind: Literal["decision", "deploy"] = "decision"
    title: str
    context: str | None = None
    options: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    urgency: Literal["low", "normal", "high"] = "normal"
