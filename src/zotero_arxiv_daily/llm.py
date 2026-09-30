"""Stop repeated requests after a definitive unavailable-model response."""
from threading import Event, Lock


class ModelUnavailableError(RuntimeError):
    pass


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


class ModelRequests:
    """One initial request gates concurrent enrichment; later successes run in parallel."""
    def __init__(self):
        self._lock = Lock()
        self._checked = False
        self._unavailable = Event()

    @property
    def unavailable(self):
        return self._unavailable.is_set()

    def _call(self, operation):
        try:
            return operation()
        except Exception as exc:
            if model_unavailable(exc):
                self._unavailable.set()
            raise

    def call(self, operation):
        with self._lock:
            if self.unavailable:
                raise ModelUnavailableError('Configured model is unavailable for this run')
            if not self._checked:
                # Only a successful probe opens concurrency. A generic 404 or
                # transient error must not disable the model for subsequent papers.
                result = self._call(operation)
                self._checked = True
                return result
        return self._call(operation)
