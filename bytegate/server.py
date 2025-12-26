"""
Bytegate Server

Manages WebSocket connections from remote systems and bridges them with Redis lists.
The server is completely content-agnostic - it just moves bytes.

This module provides a standalone server for use outside of FastAPI.
For FastAPI integration, use the router module instead.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from websockets.asyncio.server import ServerConnection

from bytegate.config import BytegateConfig
from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)


@dataclass
class GatewayServer:
    """
    Gateway server for bridging WebSocket connections with Redis.

    This class manages multiple WebSocket connections and handles the
    Redis list communication for each.

    Usage:
        server = GatewayServer(redis, server_id="pod-1")
        await server.handle_connection(connection_id, websocket)
    """

    redis: "Redis"
    server_id: str
    config: BytegateConfig = field(default_factory=BytegateConfig)
    active_connections_set: set[str] = field(default_factory=set, init=False)
    on_connect: Callable[[str], Awaitable[None]] | None = field(default=None, kw_only=True)
    on_disconnect: Callable[[str], Awaitable[None]] | None = field(default=None, kw_only=True)

    @property
    def active_connections(self) -> list[str]:
        """List of currently active connection IDs."""
        return list(self.active_connections_set)

    async def handle_connection(
        self,
        connection_id: str,
        websocket: "ServerConnection",
    ) -> None:
        """
        Handle a WebSocket connection lifecycle.

        This method blocks until the connection closes.
        """
        self.active_connections_set.add(connection_id)

        try:
            await self.register(connection_id)

            if self.on_connect:
                await self.on_connect(connection_id)

            await self.run_connection(connection_id, websocket)

        finally:
            await self.unregister(connection_id)
            self.active_connections_set.discard(connection_id)

            if self.on_disconnect:
                await self.on_disconnect(connection_id)

    async def register(self, connection_id: str) -> None:
        """Register connection in Redis."""
        await self.redis.hset(self.config.connections_hash, connection_id, self.server_id)
        LOG.info("Registered connection %s on server %s", connection_id, self.server_id)

    async def unregister(self, connection_id: str) -> None:
        """Remove connection from Redis."""
        await self.redis.hdel(self.config.connections_hash, connection_id)
        LOG.info("Unregistered connection %s", connection_id)

    async def run_connection(
        self,
        connection_id: str,
        websocket: "ServerConnection",
    ) -> None:
        """Main loop: bridge Redis lists with WebSocket."""
        pending_requests: dict[str, asyncio.Future[bytes]] = {}

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.heartbeat(connection_id))
            tg.create_task(self.redis_to_websocket(connection_id, websocket, pending_requests))
            tg.create_task(self.websocket_to_redis(websocket, pending_requests))

    async def heartbeat(self, connection_id: str) -> None:
        """Periodically refresh connection registration."""
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            await self.redis.hset(self.config.connections_hash, connection_id, self.server_id)

    async def redis_to_websocket(
        self,
        connection_id: str,
        websocket: "ServerConnection",
        pending_requests: dict[str, asyncio.Future[bytes]],
    ) -> None:
        """Block on Redis list and forward requests to WebSocket."""
        tx_list = self.config.tx_list(connection_id)
        processing_list = self.config.tx_processing_list(connection_id)

        while True:
            data = await self.redis.brpoplpush(tx_list, processing_list, timeout=1)
            if data is None:
                continue

            await self.handle_redis_message(
                websocket,
                data,
                pending_requests,
                processing_list,
            )

    async def handle_redis_message(
        self,
        websocket: "ServerConnection",
        data: bytes | str,
        pending_requests: dict[str, asyncio.Future[bytes]],
        processing_list: str,
    ) -> None:
        """Process a request from Redis, forward to WebSocket, wait for response."""
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            envelope = GatewayEnvelope.model_validate_json(data)
            request_id = envelope.request_id

            LOG.debug("Forwarding request %s to WebSocket", request_id)

            # Track this request
            response_future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
            pending_requests[request_id] = response_future

            try:
                # Forward payload to WebSocket (transparent - raw bytes)
                await websocket.send(envelope.payload)

                # Wait for response
                try:
                    response_payload = await asyncio.wait_for(
                        response_future,
                        timeout=self.config.response_timeout_seconds,
                    )
                    response = GatewayResponse(request_id=request_id, payload=response_payload)
                except TimeoutError:
                    response = GatewayResponse(
                        request_id=request_id,
                        payload=b"",
                        error="Timeout waiting for WebSocket response",
                    )
            finally:
                pending_requests.pop(request_id, None)

            # Publish response to Redis
            rx_list = self.config.rx_list(envelope.connection_id)
            await self.redis.lpush(rx_list, response.model_dump_json())

        except Exception:
            LOG.exception("Error handling Redis message")
        finally:
            await self.redis.lrem(processing_list, 1, data)

    async def websocket_to_redis(
        self,
        websocket: "ServerConnection",
        pending_requests: dict[str, asyncio.Future[bytes]],
    ) -> None:
        """Receive messages from WebSocket and resolve pending requests."""
        async for message in websocket:
            # Ensure we have bytes
            if isinstance(message, str):
                message = message.encode("utf-8")

            # Match response to oldest pending request (FIFO order)
            if pending_requests:
                request_id = next(iter(pending_requests))
                future = pending_requests.get(request_id)
                if future and not future.done():
                    future.set_result(message)
            else:
                LOG.debug("Dropping unsolicited WebSocket message (%d bytes)", len(message))

    async def shutdown(self) -> None:
        """Gracefully shutdown the server."""
        LOG.info("Shutting down bytegate server %s", self.server_id)
