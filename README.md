# bytegate

A transparent Redis-backed WebSocket gateway that relays bytes between distributed systems.

[![PyPI - Version](https://img.shields.io/pypi/v/bytegate)](https://pypi.org/project/bytegate/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/bytegate)](https://pypi.org/project/bytegate/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## What is bytegate?

Bytegate is a **transparent transport layer** that connects WebSocket clients to your backend services through Redis pub/sub. It acts as a relay - it doesn't inspect, validate, or transform your data. It just moves bytes.

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│  Remote System  │◄──WS───►│    Bytegate     │◄─Redis─►│   Your API      │
│  (WebSocket)    │         │    Server       │         │   (Client)      │
└─────────────────┘         └─────────────────┘         └─────────────────┘
```

### Why bytegate?

- **Transparent**: Payloads are raw bytes - encode/decode however you want
- **Simple**: One concept (connection_id), two components (client/server)
- **Scalable**: Multiple API pods can send to any connected WebSocket via Redis
- **Async-native**: Built on `asyncio`, `redis-py`, and `websockets`
- **Framework-friendly**: FastAPI router included, or use standalone

## Installation

```bash
pip install bytegate

# With FastAPI support
pip install bytegate[fastapi]
```

## Quick Start

### Server Side (WebSocket Handler)

The server accepts WebSocket connections and bridges them with Redis:

```python
from fastapi import FastAPI
from redis import asyncio as aioredis

from bytegate import router

app = FastAPI()
app.include_router(router)

@app.on_event("startup")
async def startup():
    app.extra["redis"] = aioredis.from_url("redis://localhost")
    app.extra["server_id"] = "pod-1"  # Unique identifier for this server
```

Remote systems connect via WebSocket to `/bytegate/{connection_id}`.

### Client Side (Send Messages)

Any service can send messages to connected WebSocket clients:

```python
from redis import asyncio as aioredis
from bytegate import GatewayClient

redis = aioredis.from_url("redis://localhost")
client = GatewayClient(redis)

# Check if a connection exists
if await client.is_connected("device-123"):
    # Send bytes and wait for response
    response = await client.send("device-123", b'{"command": "ping"}')
    print(response.payload)  # Raw bytes from the remote system

# Fire-and-forget (no response)
request_id = await client.send_no_wait("device-123", b"heartbeat")
```

## Architecture

### How It Works

1. **Remote system connects** via WebSocket → Server registers `connection_id` in Redis
2. **API sends message** → Client publishes to Redis channel `bytegate:{connection_id}:request`
3. **Server receives** → Forwards payload bytes to WebSocket, waits for response
4. **Response flows back** → Server publishes to Redis list `bytegate:response:{request_id}`
5. **API receives response** → Client returns the bytes to caller

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Bytes-only payload** | You decide encoding. JSON? Protobuf? MessagePack? Your choice. |
| **FIFO response matching** | Simple request-response pattern. One request, one response. |
| **Redis pub/sub + lists** | Pub/sub for fan-out, lists for reliable response delivery. |
| **Heartbeat registration** | Connections re-register every 10s. Stale entries auto-expire. |

## API Reference

### `GatewayClient`

```python
client = GatewayClient(redis: aioredis.Redis)

# Check connection status
await client.is_connected(connection_id: str) -> bool

# Send and wait for response
await client.send(
    connection_id: str,
    payload: bytes,
    timeout: float = 30.0
) -> GatewayResponse

# Send without waiting
await client.send_no_wait(
    connection_id: str,
    payload: bytes
) -> str  # Returns request_id
```

### `GatewayServer` (Standalone)

For non-FastAPI usage:

```python
from bytegate import GatewayServer

server = GatewayServer(
    redis=redis,
    server_id="my-server",
    on_connect=async_callback,    # Optional
    on_disconnect=async_callback  # Optional
)

# Handle a websocket connection (blocks until disconnect)
await server.handle_connection(connection_id, websocket)
```

### Models

```python
from bytegate import GatewayEnvelope, GatewayResponse

# Request envelope (internal, but useful for testing)
envelope = GatewayEnvelope(
    connection_id="device-123",
    payload=b"raw bytes here"
)
# request_id is auto-generated

# Response from remote system
response = GatewayResponse(
    request_id="abc123",
    payload=b"response bytes",
    error=None  # Or error message string
)
```

### Errors

```python
from bytegate import BytegateError, ConnectionNotFound, GatewayTimeout

try:
    response = await client.send("device-123", b"data")
except ConnectionNotFound as e:
    print(f"Device {e.connection_id} not connected")
except GatewayTimeout as e:
    print(f"Timeout after {e.timeout}s waiting for {e.connection_id}")
except BytegateError:
    print("Other bytegate error")
```

## Redis Keys

| Key Pattern | Type | Purpose |
|-------------|------|---------|
| `bytegate:connections` | Hash | Maps `connection_id` → `server_id` |
| `bytegate:{connection_id}:request` | Pub/Sub | Channel for incoming requests |
| `bytegate:response:{request_id}` | List | Response queue (TTL: 60s) |

## Requirements

- Python 3.11+
- Redis 5.0+ (for streams/pub-sub)
- `redis[hiredis]` (recommended for performance)
- `pydantic` 2.0+
- `websockets` 12.0+
- `fastapi` 0.100+ (optional, for router)

## License

MIT License - see [LICENSE](LICENSE) for details.
