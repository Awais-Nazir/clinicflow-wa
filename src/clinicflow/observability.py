"""Opt-in error reporting with a narrow outbound event schema."""

import os


def configure_error_reporting() -> None:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_source_context=False,
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        auto_session_tracking=False,
        traces_sample_rate=0.0,
        before_send=redact_error_event,
    )


def redact_error_event(event: dict, hint: dict | None = None) -> dict | None:
    """Keep exception classes and code locations. Drop raw messages and context."""

    exceptions = event.get("exception", {}).get("values", [])
    if not exceptions:
        return None
    safe_exceptions = []
    for exception in exceptions:
        frames = [
            {
                "filename": frame.get("filename", "").replace("\\", "/").rsplit("/", 1)[-1],
                "function": frame.get("function"),
                "lineno": frame.get("lineno"),
            }
            for frame in exception.get("stacktrace", {}).get("frames", [])
        ]
        safe_exceptions.append({
            "type": exception.get("type", "Error"),
            "module": exception.get("module"),
            "stacktrace": {"frames": frames},
        })
    safe = {
        key: event[key] for key in (
            "event_id", "timestamp", "platform", "level", "release", "environment",
        ) if key in event
    }
    safe["exception"] = {"values": safe_exceptions}
    return safe
