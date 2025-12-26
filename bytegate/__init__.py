"""
bytegate - Redis-backed WebSocket Gateway

A transparent transport layer that relays bytes between API consumers
and remote WebSocket-connected systems via Redis pub/sub.

The gateway does NOT inspect message payloads - it simply moves bytes.
"""

from bytegate.client import GatewayClient
from bytegate.errors import (
    BytegateError,
    ConnectionNotFound,
    GatewayTimeout,
)
from bytegate.models import (
    GatewayEnvelope,
    GatewayResponse,
)
from bytegate.server import GatewayServer

__all__ = [
    # Client
    "GatewayClient",
    # Server
    "GatewayServer",
    # Models
    "GatewayEnvelope",
    "GatewayResponse",
    # Errors
    "BytegateError",
    "ConnectionNotFound",
    "GatewayTimeout",
]

__version__ = "0.1.0"
