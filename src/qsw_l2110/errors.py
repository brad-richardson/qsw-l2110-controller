class QswError(Exception):
    """Base error for the controller."""


class AuthenticationError(QswError):
    """The switch did not establish an authenticated session."""


class ApiError(QswError):
    """The private switch API returned an unexpected response."""


class ConfigError(QswError):
    """The desired configuration is invalid or unsafe."""


class VerificationError(QswError):
    """A write did not produce the expected read-back state."""
