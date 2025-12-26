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

from bytegate.config import BytegateConfig
from bytegate.models import GatewayEnvelope, GatewayResponse

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/bytegate", tags=["bytegate"])


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
    config = websocket.app.extra.get("bytegate_config")
    if config is None:
        config = BytegateConfig()
    elif not isinstance(config, BytegateConfig):
        config = BytegateConfig.model_validate(config)

    try:
        async with connection_lifecycle(redis, connection_id, server_id, config):
            await run_connection(websocket, redis, connection_id, config)
    except WebSocketDisconnect:
        LOG.info("Bytegate connection disconnected: %s", connection_id)
    except Exception:
        LOG.exception("Error in bytegate connection: %s", connection_id)


@asynccontextmanager
async def connection_lifecycle(
    redis: "Redis",
    connection_id: str,
    server_id: str,
    config: BytegateConfig,
) -> AsyncIterator[None]:
    """Register/unregister connection in Redis with heartbeat."""
    await redis.hset(config.connections_hash, connection_id, server_id)
    LOG.info("Registered connection %s on server %s", connection_id, server_id)

    heartbeat_task = asyncio.create_task(heartbeat(redis, connection_id, server_id, config))

    try:
        yield
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

        await redis.hdel(config.connections_hash, connection_id)
        LOG.info("Unregistered connection %s", connection_id)


async def heartbeat(
    redis: "Redis",
    connection_id: str,
    server_id: str,
    config: BytegateConfig,
) -> None:
    """Periodically refresh connection registration."""
    while True:
        await asyncio.sleep(config.heartbeat_interval_seconds)
        await redis.hset(config.connections_hash, connection_id, server_id)


async def run_connection(
    websocket: WebSocket,
    redis: "Redis",
    connection_id: str,
    config: BytegateConfig,
) -> None:
    """Main connection loop: bridge Redis lists with WebSocket."""
    pending_requests: dict[str, asyncio.Future[bytes]] = {}

    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            redis_to_websocket(websocket, redis, connection_id, pending_requests, config)
        )
        tg.create_task(websocket_to_redis(websocket, pending_requests))


async def redis_to_websocket(
    websocket: WebSocket,
    redis: "Redis",
    connection_id: str,
    pending_requests: dict[str, asyncio.Future[bytes]],
    config: BytegateConfig,
) -> None:
    """Block on Redis list and forward requests to WebSocket."""
    tx_list = config.tx_list(connection_id)
    processing_list = config.tx_processing_list(connection_id)

    while True:
        data = await redis.brpoplpush(tx_list, processing_list, timeout=1)
        if data is None:
            continue

        await handle_redis_message(
            websocket,
            redis,
            data,
            pending_requests,
            processing_list,
            config,
        )


async def handle_redis_message(
    websocket: WebSocket,
    redis: "Redis",
    data: bytes | str,
    pending_requests: dict[str, asyncio.Future[bytes]],
    processing_list: str,
    config: BytegateConfig,
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
            await websocket.send_bytes(envelope.payload)

            # Wait for response
            try:
                response_payload = await asyncio.wait_for(
                    response_future,
                    timeout=config.response_timeout_seconds,
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
        rx_list = config.rx_list(envelope.connection_id)
        await redis.lpush(rx_list, response.model_dump_json())

    except ValidationError:
        LOG.exception("Invalid message format from Redis")
    except Exception:
        LOG.exception("Error handling Redis message")
    finally:
        await redis.lrem(processing_list, 1, data)


async def websocket_to_redis(
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
        else:
            LOG.debug("Dropping unsolicited WebSocket message (%d bytes)", len(message))
