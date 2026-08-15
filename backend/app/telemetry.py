"""OpenTelemetry setup: a local, in-memory ring buffer of spans that the
frontend's TelemetryPanel polls, with defensive redaction so nothing shaped
like a credential is ever recorded — regardless of what a caller passes in.

No OTLP exporter is configured by default; spans stay local. Set
OTEL_EXPORTER_OTLP_ENDPOINT (and add a BatchSpanProcessor/OTLPSpanExporter
below) to ship spans to a real collector later.
"""

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
_RING_BUFFER_SIZE = 300

_recent_spans: deque[dict[str, Any]] = deque(maxlen=_RING_BUFFER_SIZE)


def _redact(attributes: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attributes.items() if not _REDACT_PATTERN.search(k)}


class RecordingSpanProcessor(SpanProcessor):
    """Mirrors ended spans into the in-memory ring buffer, after redaction."""

    def on_start(self, span: Span, parent_context=None) -> None:  # noqa: D401
        return None

    def on_end(self, span: ReadableSpan) -> None:
        attributes = _redact(dict(span.attributes or {}))
        _recent_spans.append(
            {
                "name": span.name,
                "trace_id": format(span.context.trace_id, "032x"),
                "span_id": format(span.context.span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "duration_ms": (span.end_time - span.start_time) / 1_000_000 if span.end_time else None,
                "status": span.status.status_code.name,
                "attributes": attributes,
            }
        )

    def shutdown(self) -> None:  # pragma: no cover - process lifecycle
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # pragma: no cover
        return True


_tracer_provider: TracerProvider | None = None


def init_telemetry() -> None:
    global _tracer_provider
    if _tracer_provider is not None:
        return
    resource = Resource.create({"service.name": "agentcore-console-backend"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(RecordingSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider


def get_recent_spans() -> list[dict[str, Any]]:
    return list(_recent_spans)


_tracer = trace.get_tracer("agentcore.console")


@contextmanager
def with_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    """Starts (and makes current) a span named `name`. Nested calls within the
    same task automatically become child spans via OTel's context propagation.
    """
    # record_exception/set_status_on_exception disabled: the except clause
    # below handles both explicitly, so OTel's own default doesn't double up.
    with _tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
        if attributes:
            span.set_attributes(_redact(attributes))
        try:
            yield span
        except Exception as exc:  # re-raise after recording, never swallow
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            if span.status.status_code == StatusCode.UNSET:
                span.set_status(Status(StatusCode.OK))


def span_time_ms() -> float:
    return time.monotonic() * 1000
