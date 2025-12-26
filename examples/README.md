# Examples

Local testing helpers for bytegate.

## Setup

```bash
uv pip install -e ".[dev,fastapi]"
```

## Run the FastAPI gateway

```bash
docker compose -f examples/docker-compose.yml up -d
uv run uvicorn examples.fastapi_app:app --reload
```

This exposes the WebSocket endpoint at `ws://127.0.0.1:8000/bytegate/{connection_id}`.
The app uses a FastAPI lifespan context to initialize a Redis client and stores it on
`app.extra["redis"]`, which the bytegate router reads per connection. It also sets
`app.extra["server_id"]` to tag connections in the Redis registry.

## Start a WebSocket echo client

```bash
uv run python examples/ws_echo_client.py --connection-id device-123
```

This connects to the gateway WebSocket endpoint and echoes any received bytes back.
The gateway forwards requests from Redis to this socket, and whatever the client
echoes becomes the response payload.

## Send a request from the API side

```bash
uv run python examples/send_request.py --connection-id device-123 --payload "hello"
```

This script creates a `GatewayClient`, enqueues a request on the per-connection
`tx` list, and blocks until a matching response arrives on the `rx` list. For local
testing you can reuse the same `connection_id` across runs to keep the echo loop simple.
