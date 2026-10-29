"""Synchronous notifications with immutable snapshots and deterministic ordering."""
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import json
from types import MappingProxyType


def snapshot(value):
    if isinstance(value, dict):
        return MappingProxyType({key:snapshot(item) for key,item in value.items()})
    if isinstance(value, list):
        return tuple(snapshot(item) for item in value)
    return value


@dataclass(frozen=True)
class Event:
    name: str
    payload: object
    invocation_id: str | None = None


class ArchiveError(RuntimeError):
    """Mandatory evidence could not be saved; the run must not continue silently."""


def record_required(record, tool, **fields):
    try:
        record(tool, **fields)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError('Could not archive '+tool) from exc


class CriticalSubscriberError(RuntimeError):
    def __init__(self, event, subscriber_id, subscriber, cause):
        super().__init__('Critical subscriber '+subscriber+' failed: '+str(cause))
        self.event = event.name
        self.invocation_id = event.invocation_id
        self.subscriber_id, self.subscriber = subscriber_id, subscriber
        # Filled by the registry if execution already produced a result.
        self.result = None


class EventBus:
    def __init__(self, record):
        self._record = record
        self._subscribers = {}
        self._publishing = False
        self._subscription_sequence = 0
        self._invocation = ContextVar('tool_invocation', default=None)

    @property
    def invocation_id(self):
        return self._invocation.get()

    @contextmanager
    def invocation(self, invocation_id):
        token = self._invocation.set(invocation_id)
        try:
            yield
        finally:
            self._invocation.reset(token)

    def record(self, tool, **fields):
        if self.invocation_id is not None:
            fields['invocation_id'] = self.invocation_id
        record_required(self._record, tool, **fields)

    def subscribe(self, name, callback, *, critical=False):
        if not callable(callback):
            raise TypeError('Subscriber must be callable')
        self._subscription_sequence += 1
        subscriber_id = 'subscriber-'+str(self._subscription_sequence)
        subscriber = getattr(callback, '__module__', type(callback).__module__)+'.'+getattr(callback, '__qualname__', type(callback).__qualname__)
        self._subscribers.setdefault(name, []).append((callback, critical, subscriber_id, subscriber))
        return subscriber_id

    def emit(self, event_name, **payload):
        if self._publishing:
            raise RuntimeError('Recursive event publication is not supported')
        subscribers = tuple(self._subscribers.get(event_name, ()))
        # Raw event recording is mandatory; failures never become optional errors.
        self.record('tool_event', event=event_name, payload=payload)
        event = Event(event_name, snapshot(json.loads(json.dumps(payload, allow_nan=False))), self.invocation_id)
        self._publishing = True
        try:
            for callback, critical, subscriber_id, subscriber in subscribers:
                try:
                    if callback(event) is not None:
                        raise TypeError('Event subscribers must not return control decisions; use a hook')
                except ArchiveError:
                    raise
                except Exception as exc:
                    self.record('subscriber_error', event=event_name, subscriber_id=subscriber_id,
                        subscriber=subscriber, critical=critical, error=type(exc).__name__, message=str(exc)[:1500])
                    if critical:
                        raise CriticalSubscriberError(event, subscriber_id, subscriber, exc) from exc
        finally:
            self._publishing = False
