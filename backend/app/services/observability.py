"""Structured events: one JSON log line + one row in `events` per event. No chain-of-thought."""
import contextvars
import json
import logging

from app.models.db import Event

log = logging.getLogger("events")

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class EventLogger:
    def __init__(self, session_factory):
        self._sf = session_factory

    def emit(self, name: str, conversation_id: str | None = None, **payload) -> None:
        rid = request_id_var.get()
        log.info(json.dumps({"event": name, "request_id": rid, "conversation_id": conversation_id, **payload}, default=str))
        try:
            with self._sf() as s:  # separate session: events survive a rolled-back request
                s.add(Event(request_id=rid, conversation_id=conversation_id, name=name, payload=payload))
                s.commit()
        except Exception as exc:  # observability must never break the request
            log.warning("event persist failed: %s", exc)
