"""Typed failures. Nothing in this application exits on a bare traceback."""

from __future__ import annotations


class NomadGuardError(Exception):
    """Base class for every failure the application reports as data."""


class ConfigError(NomadGuardError):
    """Invalid or missing configuration. Raised before any provider call."""


class CapabilityMismatch(NomadGuardError):
    """The configured model cannot satisfy a requested duration, ratio or input."""


class QAFailed(NomadGuardError):
    """A generated asset did not clear the quality gate."""


class JobBusy(NomadGuardError):
    """Another process or request already holds this job."""


class JobStateError(NomadGuardError):
    """A transition that the job's current state does not allow."""


class ProviderError(NomadGuardError):
    """A provider call failed in a classified way."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_message: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_message = provider_message
        self.request_id = request_id

    def as_dict(self) -> dict[str, object]:
        return {
            "class": type(self).__name__,
            "message": str(self),
            "retryable": self.retryable,
            "status_code": self.status_code,
            "provider_message": self.provider_message,
            "request_id": self.request_id,
        }


class RateLimited(ProviderError):
    """HTTP 429. Retry after the provider's own delay."""

    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None, **kw) -> None:
        super().__init__(message, **kw)
        self.retry_after_s = retry_after_s


class ProviderUnavailable(ProviderError):
    """5xx, transport failure or timeout."""

    retryable = True


class TransportFailure(ProviderUnavailable):
    """No HTTP response at all: connection refused, DNS, timeout."""


class AmbiguousResponse(ProviderUnavailable):
    """A 2xx whose body cannot be read, so the remote state is unknown."""


class InsufficientBalance(ProviderError):
    """HTTP 402. Retrying spends nothing; the account needs funding."""


class SafetyRefusal(ProviderError):
    """HTTP 422. The same text will be refused again."""


class InvalidRequest(ProviderError):
    """HTTP 400/401/403/404. A configuration bug, not a hiccup."""


class TaskFailed(ProviderError):
    """The provider accepted the job and then reported it failed."""


class SubmissionUnknown(ProviderError):
    """A create call may or may not have reached the provider.

    Never auto-retried: the remote task may exist and bill.
    """


def error_for_status(
    status_code: int,
    *,
    provider_message: str | None = None,
    request_id: str | None = None,
    retry_after_s: float | None = None,
) -> ProviderError:
    detail = provider_message or f"HTTP {status_code}"
    kw = {
        "status_code": status_code,
        "provider_message": provider_message,
        "request_id": request_id,
    }
    if status_code == 429:
        return RateLimited(detail, retry_after_s=retry_after_s, **kw)
    if status_code == 402:
        return InsufficientBalance(detail, **kw)
    if status_code == 422:
        return SafetyRefusal(detail, **kw)
    if status_code in (400, 401, 403, 404):
        return InvalidRequest(detail, **kw)
    if status_code >= 500:
        return ProviderUnavailable(detail, **kw)
    return ProviderError(detail, **kw)
