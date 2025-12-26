"""
Bytegate Client

Sends messages to remote systems via Redis pub/sub.
The client is completely content-agnostic - it just moves bytes.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

from bytegate.errors import ConnectionNotFound, GatewayTimeout
from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)

# Redis key patterns
CONNECTIONS_KEY = "bytegate:connections"
REQUEST_CHANNEL_PATTERN = "bytegate:{connection_id}:request"
RESPONSE_KEY_PATTERN = "bytegate:response:{request_id}"

# Default timeout for waiting on responses
DEFAULT_TIMEOUT_SECONDS = 30.0


class GatewayClient:
    """
    Client for sending messages through the Redis gateway.

    Usage:
        client = GatewayClient(redis)
        response = await client.send("my-connection-id", b'{"method": "ping"}')
    """

    def __init__(self, redis: "Redis") -> None:
        self._redis = redis

    async def is_connected(self, connection_id: str) -> bool:
        """Check if a connection is registered in the gateway."""
        result = await self._redis.hexists(CONNECTIONS_KEY, connection_id)  # type: ignore[misc]
        return bool(result)

    async def send(
        self,
        connection_id: str,
        payload: bytes,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> GatewayResponse:
        """
        Send a message to a remote system and wait for a response.

        Args:
            connection_id: Identifier for the remote connection
            payload: Message payload as bytes (opaque - not inspected)
            timeout: Maximum time to wait for response

        Returns:
            GatewayResponse containing the response payload

        Raises:
            ConnectionNotFound: If the connection is not registered
            GatewayTimeout: If no response is received within timeout
        """
        if not await self.is_connected(connection_id):
            raise ConnectionNotFound(connection_id)

        envelope = GatewayEnvelope(connection_id=connection_id, payload=payload)
        request_id = envelope.request_id

        LOG.debug("Sending request %s to connection %s", request_id, connection_id)

        channel = REQUEST_CHANNEL_PATTERN.format(connection_id=connection_id)
        await self._redis.publish(channel, envelope.model_dump_json())

        response_key = RESPONSE_KEY_PATTERN.format(request_id=request_id)
        result = await self._redis.blpop([response_key], timeout=timeout)  # type: ignore[misc]

        if result is None:
            LOG.warning("Timeout waiting for response to request %s", request_id)
            raise GatewayTimeout(connection_id, timeout)

        _, response_data = result
        response = GatewayResponse.model_validate_json(response_data)

        LOG.debug("Received response for request %s", request_id)
        return response

    async def send_no_wait(self, connection_id: str, payload: bytes) -> str:
        """
        Send a message without waiting for a response.

        Returns the request_id for tracking purposes.

        Raises:
            ConnectionNotFound: If the connection is not registered
        """
        if not await self.is_connected(connection_id):
            raise ConnectionNotFound(connection_id)

        envelope = GatewayEnvelope(connection_id=connection_id, payload=payload)

        channel = REQUEST_CHANNEL_PATTERN.format(connection_id=envelope.connection_id)
        await self._redis.publish(channel, envelope.model_dump_json())

        return envelope.request_id
