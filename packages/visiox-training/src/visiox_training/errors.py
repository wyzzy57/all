class FrameworkAdapterError(Exception):
    """Base error for framework adapter selection and registration."""


class DuplicateAdapterError(FrameworkAdapterError):
    """Raised when an adapter identity is already registered."""


class InvalidAdapterVersionError(FrameworkAdapterError):
    """Raised when an adapter version is not a valid PEP 440 version."""


class AmbiguousAdapterError(FrameworkAdapterError):
    """Raised when adapter resolution has multiple equally valid results."""


class UnknownAdapterError(FrameworkAdapterError):
    """Raised when an adapter key or legacy engine is unknown."""


class UnknownAdapterVersionError(FrameworkAdapterError):
    """Raised when an adapter key exists but the requested version does not."""


class UnsupportedTaskFrameworkError(FrameworkAdapterError):
    """Raised when a framework does not support a requested task type."""


class UnsupportedAdapterOperationError(FrameworkAdapterError):
    """Raised when a catalog operation is not wired for execution yet."""
