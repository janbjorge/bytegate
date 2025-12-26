"""
Bytegate errors.

Simple hierarchy: one base class, minimal specific exceptions.
"""


class BytegateError(Exception):
    """Base exception for all bytegate errors."""


class ConnectionNotFound(BytegateError):
    """The requested connection is not registered in the gateway."""

    def __init__(self, connection_id: str) -> None:
        self.connection_id = connection_id
        super().__init__(f"Connection not found: {connection_id}")


class GatewayTimeout(BytegateError):
    """Timed out waiting for a response from the remote system."""

    def __init__(self, connection_id: str, timeout: float) -> None:
        self.connection_id = connection_id
        self.timeout = timeout
        super().__init__(f"Timeout after {timeout}s waiting for response from: {connection_id}")
