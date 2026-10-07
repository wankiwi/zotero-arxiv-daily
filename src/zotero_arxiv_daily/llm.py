"""Bounded request policy and a run-wide circuit breaker; no provider bodies in diagnostics."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from collections.abc import Mapping
from threading import Event, Lock
from time import monotonic, sleep

from openai import APIConnectionError, APITimeoutError


class ModelUnavailableError(RuntimeError):
    reason = 'model_unavailable'


class SummaryUnavailable(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def request_policy(config):
    supplied = config.get('request', {})
    if not isinstance(supplied, Mapping):
        raise ValueError('llm.request must be a mapping')
    # Absent policy preserves the one-attempt contract of old direct callers.
    defaults = {'timeout_seconds': 30, 'max_attempts': 1,
                'retry_backoff_seconds': 1, 'max_retry_wait_seconds': 5,
                'failure_threshold': 3}
    limits = {'timeout_seconds': (1, 60), 'max_attempts': (1, 3),
              'retry_backoff_seconds': (0.1, 5), 'max_retry_wait_seconds': (0.1, 30),
              'failure_threshold': (2, 5)}
    result = {}
    for key, default in defaults.items():
        value = supplied.get(key, default)
        lower, upper = limits[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'llm.request.{key} must be between {lower} and {upper}')
        if key in ('max_attempts', 'failure_threshold') and type(value) is not int:
            raise ValueError(f'llm.request.{key} must be an integer')
        result[key] = value
    return result


def model_unavailable(exc):
    if isinstance(exc, ModelUnavailableError):
        return True
    if getattr(exc, 'status_code', None) != 404:
        return False
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        error = body.get('error', body)
        if isinstance(error, dict) and error.get('code') in ('model_not_found', 'model_unavailable'):
            return True
    message = str(body if body is not None else exc).casefold()
    return any(phrase in message for phrase in (
        'this model is unavailable', 'model is not available',
        'model does not exist', 'no endpoints found for',
    ))


def failure_reason(exc):
    if model_unavailable(exc):
        return 'model_unavailable'
    if isinstance(exc, SummaryUnavailable):
        return exc.reason
    status = getattr(exc, 'status_code', None)
    if status in (401, 403, 429, 400, 422):
        return {401: 'authentication_failed', 403: 'access_denied', 429: 'rate_limited',
                400: 'request_rejected', 422: 'request_rejected'}[status]
    if isinstance(exc, (TimeoutError, APITimeoutError)):
        return 'request_timeout'
    if isinstance(exc, APIConnectionError):
        return 'connection_failed'
    if status in (408, 500, 502, 503, 504):
        return 'provider_unavailable'
    return 'request_failed'


def transient_failure(exc):
    return failure_reason(exc) in ('request_timeout', 'connection_failed', 'provider_unavailable')


def retry_after_seconds(exc):
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', {})
    raw = headers.get('retry-after')
    if raw is None:
        return 0.0
    try:
        seconds = float(raw)
    except (ValueError, TypeError):
        try:
            instant = parsedate_to_datetime(raw)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            seconds = (instant - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return math.inf  # Uninterpretable provider restriction: do not retry.
    return max(0.0, seconds) if math.isfinite(seconds) else math.inf


class ModelRequests:
    """Probe once before concurrency; stop queued requests on definitive rejection/outage."""
    def __init__(self, *, failure_threshold=None, max_retry_wait=5):
        self._lock = Lock()
        self._checked = False
        self._unavailable = Event()
        self.stop_reason = None
        self.failure_threshold = failure_threshold
        self._failures = 0
        self.max_retry_wait = max_retry_wait
        self._not_before = 0.0

    @property
    def unavailable(self):
        return self._unavailable.is_set()

    def stop(self, reason):
        self.stop_reason = reason

    def check_available(self):
        if self.unavailable:
            raise ModelUnavailableError('Configured model is unavailable for this run')
        if self.stop_reason:
            raise SummaryUnavailable(self.stop_reason)
        delay = self._not_before - monotonic()
        if delay > 0:
            sleep(delay)  # BudgetRequests serializes this bounded run-wide cooldown.

    def _call(self, operation):
        try:
            result = operation()
        except Exception as exc:
            reason = failure_reason(exc)
            if reason == 'model_unavailable':
                self._unavailable.set()
            elif reason in ('authentication_failed', 'access_denied', 'rate_limited', 'request_rejected'):
                self.stop(reason)  # Never retry 403/429 or continue on another paper.
            elif getattr(getattr(exc, 'response', None), 'headers', {}).get('x-should-retry', '').casefold() == 'false':
                self.stop('retry_not_permitted')
            elif transient_failure(exc):
                self._failures += 1
                cooldown = retry_after_seconds(exc)
                if cooldown > self.max_retry_wait:
                    self.stop('retry_wait_exceeded')
                elif cooldown:
                    self._not_before = monotonic() + cooldown
                if self.failure_threshold and self._failures >= self.failure_threshold:
                    self.stop('circuit_open')
            raise
        self._failures = 0
        return result

    def call(self, operation):
        with self._lock:
            self.check_available()
            if not self._checked:
                # Only a successful probe opens concurrency. A generic 404 or
                # transient error must not disable the model for subsequent papers.
                result = self._call(operation)
                self._checked = True
                return result
        return self._call(operation)
