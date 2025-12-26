"""
Bytegate Server

Manages WebSocket connections from remote systems and bridges them with Redis.
The server is completely content-agnostic - it just moves bytes.

This module provides a standalone server for use outside of FastAPI.
For FastAPI integration, use the router module instead.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from websockets.asyncio.server import ServerConnection

from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)

# Redis key patterns (must match client.py)
CONNECTIONS_KEY = "bytegate:connections"
REQUEST_CHANNEL_PATTERN = "bytegate:{connection_id}:request"
RESPONSE_KEY_PATTERN = "bytegate:response:{request_id}"
RESPONSE_KEY_TTL_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 10


class GatewayServer:
    """
    Gateway server for bridging WebSocket connections with Redis.

    This class manages multiple WebSocket connections and handles the
    Redis pub/sub communication for each.

    Usage:
        server = GatewayServer(redis, server_id="pod-1")
        await server.handle_connection(connection_id, websocket)
    """

    def __init__(
        self,
        redis: "Redis",
        server_id: str,
        *,
        on_connect: Callable[[str], Awaitable[None]] | None = None,
        on_disconnect: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._redis = redis
        self._server_id = server_id
        self._active_connections: set[str] = set()
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def active_connections(self) -> list[str]:
        """List of currently active connection IDs."""
        return list(self._active_connections)

    async def handle_connection(
        self,
        connection_id: str,
        websocket: "ServerConnection",
    ) -> None:
        """
        Handle a WebSocket connection lifecycle.

        This method blocks until the connection closes.
        """
        self._active_connections.add(connection_id)

        try:
            await self._register(connection_id)

            if self._on_connect:
                await self._on_connect(connection_id)

            await self._run_connection(connection_id, websocket)

        finally:
            await self._unregister(connection_id)
            self._active_connections.discard(connection_id)

            if self._on_disconnect:
                await self._on_disconnect(connection_id)

    async def _register(self, connection_id: str) -> None:
        """Register connection in Redis."""
        await self._redis.hset(CONNECTIONS_KEY, connection_id, self._server_id)  # type: ignore[misc]
        LOG.info("Registered connection %s on server %s", connection_id, self._server_id)

    async def _unregister(self, connection_id: str) -> None:
        """Remove connection from Redis."""
        await self._redis.hdel(CONNECTIONS_KEY, connection_id)  # type: ignore[misc]
        LOG.info("Unregistered connection %s", connection_id)

    async def _run_connection(
        self,
        connection_id: str,
        websocket: "ServerConnection",
    ) -> None:
        """Main loop: bridge Redis pub/sub with WebSocket."""
        pending_requests: dict[str, asyncio.Future[bytes]] = {}

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._heartbeat(connection_id))
            tg.create_task(self._redis_to_websocket(connection_id, websocket, pending_requests))
            tg.create_task(self._websocket_to_redis(websocket, pending_requests))

    async def _heartbeat(self, connection_id: str) -> None:
        """Periodically refresh connection registration."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await self._redis.hset(CONNECTIONS_KEY, connection_id, self._server_id)  # type: ignore[misc]

    async def _redis_to_websocket(
        self,
        connection_id: str,
        websocket: "ServerConnection",
        pending_requests: dict[str, asyncio.Future[bytes]],
    ) -> None:
        """Subscribe to Redis and forward requests to WebSocket."""
        channel_name = REQUEST_CHANNEL_PATTERN.format(connection_id=connection_id)
        pubsub = self._redis.pubsub()

        try:
            await pubsub.subscribe(channel_name)
            LOG.debug("Subscribed to channel %s", channel_name)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                await self._handle_redis_message(websocket, message["data"], pending_requests)
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.close()

    async def _handle_redis_message(
        self,
        websocket: "ServerConnection",
        data: bytes | str,
        pending_requests: dict[str, asyncio.Future[bytes]],
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

            # Forward payload to WebSocket (transparent - raw bytes)
            await websocket.send(envelope.payload)

            # Wait for response
            try:
                response_payload = await asyncio.wait_for(response_future, timeout=30.0)
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
            response_key = RESPONSE_KEY_PATTERN.format(request_id=request_id)
            await self._redis.lpush(response_key, response.model_dump_json())  # type: ignore[misc]
            await self._redis.expire(response_key, RESPONSE_KEY_TTL_SECONDS)

        except Exception:
            LOG.exception("Error handling Redis message")

    async def _websocket_to_redis(
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

    async def shutdown(self) -> None:
        """Gracefully shutdown the server."""
        LOG.info("Shutting down bytegate server %s", self._server_id)
