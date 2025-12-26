"""
Bytegate Client

Sends messages to remote systems via Redis lists.
The client is completely content-agnostic - it just moves bytes.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

from bytegate.config import BytegateConfig
from bytegate.errors import ConnectionNotFound, GatewayTimeout
from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)


@dataclass
class GatewayClient:
    """
    Client for sending messages through the Redis gateway.

    Usage:
        client = GatewayClient(redis)
        response = await client.send("my-connection-id", b'{"method": "ping"}')
    """

    redis: "Redis"
    config: BytegateConfig = field(default_factory=BytegateConfig)
    pending_responses: dict[str, GatewayResponse] = field(default_factory=dict, init=False)

    async def is_connected(self, connection_id: str) -> bool:
        """Check if a connection is registered in the gateway."""
        result = await self.redis.hexists(self.config.connections_hash, connection_id)
        return bool(result)

    async def send(
        self,
        connection_id: str,
        payload: bytes,
        *,
        timeout: float | None = None,
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

        if timeout is None:
            timeout = self.config.default_timeout_seconds

        envelope = GatewayEnvelope(connection_id=connection_id, payload=payload)
        request_id = envelope.request_id

        LOG.debug("Sending request %s to connection %s", request_id, connection_id)

        tx_list = self.config.tx_list(connection_id)
        await self.redis.lpush(tx_list, envelope.model_dump_json())

        response = await self.wait_for_response(connection_id, request_id, timeout)
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

        tx_list = self.config.tx_list(envelope.connection_id)
        await self.redis.lpush(tx_list, envelope.model_dump_json())

        return envelope.request_id

    async def wait_for_response(
        self,
        connection_id: str,
        request_id: str,
        timeout: float,
    ) -> GatewayResponse:
        cached = self.pending_responses.pop(request_id, None)
        if cached is not None:
            return cached

        rx_list = self.config.rx_list(connection_id)
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                LOG.warning("Timeout waiting for response to request %s", request_id)
                raise GatewayTimeout(connection_id, timeout)

            result = await self.redis.blpop([rx_list], timeout=remaining)
            if result is None:
                LOG.warning("Timeout waiting for response to request %s", request_id)
                raise GatewayTimeout(connection_id, timeout)

            _, response_data = result
            if isinstance(response_data, bytes):
                response_data = response_data.decode("utf-8")
            response = GatewayResponse.model_validate_json(response_data)

            if response.request_id == request_id:
                return response

            self.pending_responses[response.request_id] = response
