class SealMemoryError(Exception):
    """Base exception for SEAL Memory SDK."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(SealMemoryError):
    """Invalid or missing API key."""


class RateLimitError(SealMemoryError):
    """Rate limit exceeded."""


class NotFoundError(SealMemoryError):
    """Agent or memory not found."""
