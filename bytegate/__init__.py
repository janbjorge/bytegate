"""
bytegate - Redis-backed WebSocket Gateway

A transparent transport layer that relays bytes between API consumers
and remote WebSocket-connected systems via Redis lists.

The gateway does NOT inspect message payloads - it simply moves bytes.
"""

from bytegate._version import version as __version__
from bytegate.client import GatewayClient
from bytegate.config import BytegateConfig
from bytegate.errors import BytegateError, ConnectionNotFound, GatewayTimeout
from bytegate.models import GatewayEnvelope, GatewayResponse
from bytegate.server import GatewayServer

__all__ = [
    # Version
    "__version__",
    # Client
    "GatewayClient",
    # Server
    "GatewayServer",
    # Config
    "BytegateConfig",
    # Models
    "GatewayEnvelope",
    "GatewayResponse",
    # Errors
    "BytegateError",
    "ConnectionNotFound",
    "GatewayTimeout",
]
