"""
FastAPI Router for Bytegate WebSocket Endpoint

Provides a WebSocket endpoint for remote systems to connect to the gateway.
All payloads are raw bytes - the gateway does not inspect content.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

if TYPE_CHECKING:
    from redis.asyncio import Redis  # noqa: F401

from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/bytegate", tags=["bytegate"])

# Redis key patterns (must match client.py and server.py)
CONNECTIONS_KEY = "bytegate:connections"
REQUEST_CHANNEL_PATTERN = "bytegate:{connection_id}:request"
RESPONSE_KEY_PATTERN = "bytegate:response:{request_id}"
RESPONSE_KEY_TTL_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 10


@router.websocket("/{connection_id}")
async def bytegate_websocket(websocket: WebSocket, connection_id: str) -> None:
    """
    WebSocket endpoint for remote system connections.

    The connection_id should be a unique identifier for the remote system.
    All messages are passed through as raw bytes - the gateway does not
    inspect or validate payloads.
    """
    await websocket.accept()
    LOG.info("New bytegate connection: %s", connection_id)

    redis: Redis = websocket.app.extra["redis"]
    server_id: str = websocket.app.extra.get("server_id", "unknown")

    try:
        async with _connection_lifecycle(redis, connection_id, server_id):
            await _run_connection(websocket, redis, connection_id)
    except WebSocketDisconnect:
        LOG.info("Bytegate connection disconnected: %s", connection_id)
    except Exception:
        LOG.exception("Error in bytegate connection: %s", connection_id)


@asynccontextmanager
async def _connection_lifecycle(
    redis: "Redis",
    connection_id: str,
    server_id: str,
) -> AsyncIterator[None]:
    """Register/unregister connection in Redis with heartbeat."""
    await redis.hset(CONNECTIONS_KEY, connection_id, server_id)  # type: ignore[misc]
    LOG.info("Registered connection %s on server %s", connection_id, server_id)

    heartbeat_task = asyncio.create_task(_heartbeat(redis, connection_id, server_id))

    try:
        yield
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

        await redis.hdel(CONNECTIONS_KEY, connection_id)  # type: ignore[misc]
        LOG.info("Unregistered connection %s", connection_id)


async def _heartbeat(
    redis: "Redis",
    connection_id: str,
    server_id: str,
) -> None:
    """Periodically refresh connection registration."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await redis.hset(CONNECTIONS_KEY, connection_id, server_id)  # type: ignore[misc]


async def _run_connection(
    websocket: WebSocket,
    redis: "Redis",
    connection_id: str,
) -> None:
    """Main connection loop: bridge Redis pub/sub with WebSocket."""
    pending_requests: dict[str, asyncio.Future[bytes]] = {}

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_redis_to_websocket(websocket, redis, connection_id, pending_requests))
        tg.create_task(_websocket_to_redis(websocket, pending_requests))


async def _redis_to_websocket(
    websocket: WebSocket,
    redis: "Redis",
    connection_id: str,
    pending_requests: dict[str, asyncio.Future[bytes]],
) -> None:
    """Subscribe to Redis and forward requests to WebSocket."""
    channel_name = REQUEST_CHANNEL_PATTERN.format(connection_id=connection_id)
    pubsub = redis.pubsub()

    try:
        await pubsub.subscribe(channel_name)
        LOG.debug("Subscribed to channel %s", channel_name)

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            await _handle_redis_message(websocket, redis, message["data"], pending_requests)
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()


async def _handle_redis_message(
    websocket: WebSocket,
    redis: "Redis",
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
        await websocket.send_bytes(envelope.payload)

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
        await redis.lpush(response_key, response.model_dump_json())  # type: ignore[misc]
        await redis.expire(response_key, RESPONSE_KEY_TTL_SECONDS)

    except ValidationError:
        LOG.exception("Invalid message format from Redis")
    except Exception:
        LOG.exception("Error handling Redis message")


async def _websocket_to_redis(
    websocket: WebSocket,
    pending_requests: dict[str, asyncio.Future[bytes]],
) -> None:
    """Receive messages from WebSocket and resolve pending requests."""
    while True:
        # Receive as bytes
        message = await websocket.receive_bytes()

        # Match response to oldest pending request (FIFO order)
        if pending_requests:
            request_id = next(iter(pending_requests))
            future = pending_requests.get(request_id)
            if future and not future.done():
                future.set_result(message)
