"""Local, redacted OpenTelemetry ring buffer for MCP observability."""

from __future__ import annotations

import re
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

_REDACT_PATTERN = re.compile(r"token|secret|password|authoriz", re.IGNORECASE)
_recent_spans: deque[dict[str, Any]] = deque(maxlen=300)
_tracer_provider: TracerProvider | None = None
_tracer = trace.get_tracer("agentorchestration.mcp-todos")


def _redact(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if not _REDACT_PATTERN.search(key)}


class RecordingSpanProcessor(SpanProcessor):
    def on_start(self, span: Span, parent_context=None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        _recent_spans.append(
            {
                "name": span.name,
                "service": (span.resource.attributes or {}).get("service.name", ""),
                "trace_id": format(span.context.trace_id, "032x"),
                "span_id": format(span.context.span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "duration_ms": (span.end_time - span.start_time) / 1_000_000 if span.end_time else None,
                # The OTel default is UNSET for successful framework spans
                # (for example FastMCP tools/list). The UI uses OK as the
                # human-facing equivalent when no error was recorded.
                "status": "OK" if span.status.status_code.name == "UNSET" else span.status.status_code.name,
                "attributes": _redact(dict(span.attributes or {})),
            }
        )

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def init_telemetry() -> None:
    global _tracer_provider
    if _tracer_provider is not None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "agentorchestration-console-mcp-todos-server"}))
    provider.add_span_processor(RecordingSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider


def get_recent_spans() -> list[dict[str, Any]]:
    return list(_recent_spans)


def clear_recent_spans() -> int:
    count = len(_recent_spans)
    _recent_spans.clear()
    return count


@contextmanager
def with_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    with _tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
        if attributes:
            span.set_attributes(_redact(attributes))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            if span.status.status_code == StatusCode.UNSET:
                span.set_status(Status(StatusCode.OK))
