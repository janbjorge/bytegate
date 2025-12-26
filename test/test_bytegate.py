"""
Tests for bytegate - Redis-backed WebSocket gateway.

These tests verify the gateway's behavior as a transparent bytes transport layer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bytegate.client import (
    CONNECTIONS_KEY,
    DEFAULT_TIMEOUT_SECONDS,
    REQUEST_CHANNEL_PATTERN,
    GatewayClient,
)
from bytegate.errors import BytegateError, ConnectionNotFound, GatewayTimeout
from bytegate.models import GatewayEnvelope, GatewayResponse


class TestGatewayModels:
    """Tests for gateway message models with bytes payloads."""

    def test_envelope_generates_request_id(self) -> None:
        """Envelope should auto-generate a request_id if not provided."""
        envelope = GatewayEnvelope(connection_id="conn-1", payload=b'{"test": true}')

        assert envelope.request_id is not None
        assert len(envelope.request_id) == 32  # UUID hex

    def test_envelope_with_explicit_request_id(self) -> None:
        """Envelope should use provided request_id."""
        envelope = GatewayEnvelope(
            request_id="custom-id",
            connection_id="conn-1",
            payload=b'{"test": true}',
        )

        assert envelope.request_id == "custom-id"

    def test_envelope_payload_is_bytes(self) -> None:
        """Envelope payload should be bytes."""
        envelope = GatewayEnvelope(connection_id="conn-1", payload=b"\x00\x01\x02\x03")

        assert isinstance(envelope.payload, bytes)
        assert envelope.payload == b"\x00\x01\x02\x03"

    def test_envelope_serialization_roundtrip(self) -> None:
        """Envelope should survive JSON serialization roundtrip with bytes."""
        original = GatewayEnvelope(
            request_id="req-123",
            connection_id="conn-1",
            payload=b'{"method": "ping"}',
        )

        json_str = original.model_dump_json()
        parsed = GatewayEnvelope.model_validate_json(json_str)

        assert parsed.request_id == "req-123"
        assert parsed.connection_id == "conn-1"
        assert parsed.payload == b'{"method": "ping"}'

    def test_envelope_binary_payload_roundtrip(self) -> None:
        """Envelope should handle binary payloads through JSON serialization."""
        # Binary data that would break naive string handling
        binary_data = bytes(range(256))
        original = GatewayEnvelope(connection_id="conn-1", payload=binary_data)

        json_str = original.model_dump_json()
        parsed = GatewayEnvelope.model_validate_json(json_str)

        assert parsed.payload == binary_data

    def test_response_with_error(self) -> None:
        """Response should support optional error field."""
        response = GatewayResponse(
            request_id="req-123",
            payload=b"",
            error="Connection timeout",
        )

        assert response.error == "Connection timeout"

    def test_response_without_error(self) -> None:
        """Response error should default to None."""
        response = GatewayResponse(
            request_id="req-123",
            payload=b'{"result": "ok"}',
        )

        assert response.error is None

    def test_response_payload_is_bytes(self) -> None:
        """Response payload should be bytes."""
        response = GatewayResponse(
            request_id="req-123",
            payload=b"\xff\xfe\xfd",
        )

        assert isinstance(response.payload, bytes)
        assert response.payload == b"\xff\xfe\xfd"

    def test_response_serialization_roundtrip(self) -> None:
        """Response should survive JSON serialization roundtrip."""
        original = GatewayResponse(
            request_id="abc123",
            payload=b'{"status": "ok"}',
            error=None,
        )

        json_data = original.model_dump_json()
        restored = GatewayResponse.model_validate_json(json_data)

        assert restored.request_id == original.request_id
        assert restored.payload == original.payload
        assert restored.error == original.error


class TestGatewayErrors:
    """Tests for gateway error types."""

    def test_connection_not_found_error(self) -> None:
        """ConnectionNotFound should contain connection_id."""
        error = ConnectionNotFound("my-connection")

        assert error.connection_id == "my-connection"
        assert "my-connection" in str(error)
        assert isinstance(error, BytegateError)

    def test_gateway_timeout_error(self) -> None:
        """GatewayTimeout should contain connection_id and timeout."""
        error = GatewayTimeout("my-connection", 30.0)

        assert error.connection_id == "my-connection"
        assert error.timeout == 30.0
        assert "30" in str(error)
        assert isinstance(error, BytegateError)

    def test_bytegate_error_is_base(self) -> None:
        """BytegateError should be catchable for all gateway errors."""
        errors = [
            ConnectionNotFound("conn"),
            GatewayTimeout("conn", 10.0),
        ]

        for error in errors:
            assert isinstance(error, BytegateError)


class TestGatewayClient:
    """Tests for the gateway client."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=True)
        redis.publish = AsyncMock()
        redis.blpop = AsyncMock()
        return redis

    @pytest.fixture
    def client(self, mock_redis: AsyncMock) -> GatewayClient:
        """Create a gateway client with mock Redis."""
        return GatewayClient(mock_redis)

    async def test_is_connected_returns_true(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """is_connected should return True when connection exists."""
        mock_redis.hexists.return_value = True

        result = await client.is_connected("conn-1")

        assert result is True
        mock_redis.hexists.assert_called_once_with(CONNECTIONS_KEY, "conn-1")

    async def test_is_connected_returns_false(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """is_connected should return False when connection doesn't exist."""
        mock_redis.hexists.return_value = False

        result = await client.is_connected("conn-1")

        assert result is False

    async def test_send_raises_connection_not_found(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send should raise ConnectionNotFound if not connected."""
        mock_redis.hexists.return_value = False

        with pytest.raises(ConnectionNotFound) as exc_info:
            await client.send("conn-1", b'{"method": "ping"}')

        assert exc_info.value.connection_id == "conn-1"

    async def test_send_publishes_to_redis(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send should publish envelope to Redis channel."""
        mock_redis.hexists.return_value = True
        mock_redis.blpop.return_value = (
            b"key",
            GatewayResponse(request_id="test", payload=b'{"result": "pong"}').model_dump_json(),
        )

        await client.send("conn-1", b'{"method": "ping"}')

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        channel = call_args[0][0]
        assert channel == REQUEST_CHANNEL_PATTERN.format(connection_id="conn-1")

    async def test_send_waits_for_response(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send should wait for response via blpop."""
        mock_redis.hexists.return_value = True

        response_data = GatewayResponse(
            request_id="test-id",
            payload=b'{"result": "success"}',
        ).model_dump_json()

        mock_redis.blpop.return_value = (b"key", response_data)

        response = await client.send("conn-1", b'{"method": "test"}')

        assert response.payload == b'{"result": "success"}'
        mock_redis.blpop.assert_called_once()

    async def test_send_raises_timeout(self, client: GatewayClient, mock_redis: AsyncMock) -> None:
        """send should raise GatewayTimeout if blpop returns None."""
        mock_redis.hexists.return_value = True
        mock_redis.blpop.return_value = None

        with pytest.raises(GatewayTimeout) as exc_info:
            await client.send("conn-1", b'{"method": "ping"}', timeout=5.0)

        assert exc_info.value.connection_id == "conn-1"
        assert exc_info.value.timeout == 5.0

    async def test_send_uses_default_timeout(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send should use default timeout if not specified."""
        mock_redis.hexists.return_value = True
        mock_redis.blpop.return_value = (
            b"key",
            GatewayResponse(request_id="test", payload=b"{}").model_dump_json(),
        )

        await client.send("conn-1", b"{}")

        call_args = mock_redis.blpop.call_args
        assert call_args[1]["timeout"] == DEFAULT_TIMEOUT_SECONDS

    async def test_send_with_bytes_payload(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send should properly handle bytes payload."""
        mock_redis.hexists.return_value = True
        mock_redis.blpop.return_value = (
            b"key",
            GatewayResponse(request_id="test", payload=b"\x00\x01\x02").model_dump_json(),
        )

        # Send binary data
        response = await client.send("conn-1", b"\xff\xfe\xfd")

        # Verify the publish was called
        mock_redis.publish.assert_called_once()

        # Verify response has bytes
        assert isinstance(response.payload, bytes)
        assert response.payload == b"\x00\x01\x02"

    async def test_send_no_wait_returns_request_id(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send_no_wait should return the request_id."""
        mock_redis.hexists.return_value = True

        request_id = await client.send_no_wait("conn-1", b'{"method": "fire"}')

        assert request_id is not None
        assert len(request_id) == 32

    async def test_send_no_wait_raises_connection_not_found(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send_no_wait should raise ConnectionNotFound if not connected."""
        mock_redis.hexists.return_value = False

        with pytest.raises(ConnectionNotFound):
            await client.send_no_wait("conn-1", b'{"method": "fire"}')

    async def test_send_no_wait_with_bytes(
        self, client: GatewayClient, mock_redis: AsyncMock
    ) -> None:
        """send_no_wait should handle bytes payload."""
        mock_redis.hexists.return_value = True

        request_id = await client.send_no_wait("conn-1", b"\x00\x01\x02\x03")

        assert request_id is not None
        mock_redis.publish.assert_called_once()


class TestBytesPayloadHandling:
    """Tests specifically for bytes payload handling."""

    def test_envelope_empty_bytes(self) -> None:
        """Envelope should handle empty bytes."""
        envelope = GatewayEnvelope(connection_id="conn", payload=b"")

        json_str = envelope.model_dump_json()
        parsed = GatewayEnvelope.model_validate_json(json_str)

        assert parsed.payload == b""

    def test_envelope_large_binary(self) -> None:
        """Envelope should handle large binary payloads."""
        # 1MB of random-ish bytes
        large_payload = bytes(i % 256 for i in range(1024 * 1024))
        envelope = GatewayEnvelope(connection_id="conn", payload=large_payload)

        json_str = envelope.model_dump_json()
        parsed = GatewayEnvelope.model_validate_json(json_str)

        assert parsed.payload == large_payload

    def test_envelope_null_bytes(self) -> None:
        """Envelope should handle payloads with null bytes."""
        payload = b"hello\x00world\x00test"
        envelope = GatewayEnvelope(connection_id="conn", payload=payload)

        json_str = envelope.model_dump_json()
        parsed = GatewayEnvelope.model_validate_json(json_str)

        assert parsed.payload == payload

    def test_response_empty_bytes(self) -> None:
        """Response should handle empty bytes."""
        response = GatewayResponse(request_id="123", payload=b"")

        json_str = response.model_dump_json()
        parsed = GatewayResponse.model_validate_json(json_str)

        assert parsed.payload == b""

    def test_payload_accepts_bytes_directly(self) -> None:
        """Payload field should accept bytes without conversion."""
        data = b"\x89PNG\r\n\x1a\n"  # PNG header
        envelope = GatewayEnvelope(connection_id="conn", payload=data)

        assert envelope.payload == data
        assert isinstance(envelope.payload, bytes)


class TestGatewayIntegration:
    """Integration-style tests for gateway components."""

    @pytest.fixture
    def mock_redis_with_pubsub(self) -> tuple[AsyncMock, AsyncMock]:
        """Create a mock Redis with pub/sub support for integration tests."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=True)
        redis.hset = AsyncMock()
        redis.hdel = AsyncMock()
        redis.publish = AsyncMock()
        redis.blpop = AsyncMock()
        redis.lpush = AsyncMock()
        redis.expire = AsyncMock()

        pubsub = AsyncMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()
        redis.pubsub = MagicMock(return_value=pubsub)

        return redis, pubsub

    async def test_envelope_roundtrip(self) -> None:
        """Envelope should survive JSON serialization roundtrip."""
        original = GatewayEnvelope(
            connection_id="test-conn",
            payload=b'{"complex": {"nested": [1, 2, 3]}}',
        )

        json_data = original.model_dump_json()
        restored = GatewayEnvelope.model_validate_json(json_data)

        assert restored.connection_id == original.connection_id
        assert restored.payload == original.payload
        assert restored.request_id == original.request_id

    async def test_response_roundtrip(self) -> None:
        """Response should survive JSON serialization roundtrip."""
        original = GatewayResponse(
            request_id="abc123",
            payload=b'{"status": "ok"}',
            error=None,
        )

        json_data = original.model_dump_json()
        restored = GatewayResponse.model_validate_json(json_data)

        assert restored.request_id == original.request_id
        assert restored.payload == original.payload
        assert restored.error == original.error

    async def test_binary_protobuf_like_payload(self) -> None:
        """Gateway should handle binary payloads similar to protobuf."""
        # Simulate a protobuf-like binary payload
        proto_like = b"\x08\x96\x01\x12\x07testing\x1a\x05hello"

        envelope = GatewayEnvelope(connection_id="test", payload=proto_like)
        json_str = envelope.model_dump_json()
        restored = GatewayEnvelope.model_validate_json(json_str)

        assert restored.payload == proto_like

    async def test_msgpack_like_payload(self) -> None:
        """Gateway should handle binary payloads similar to msgpack."""
        # Simulate msgpack-like binary
        msgpack_like = b"\x82\xa4name\xa4test\xa5value\xcb@\t!\xfbTD-\x18"

        response = GatewayResponse(request_id="123", payload=msgpack_like)
        json_str = response.model_dump_json()
        restored = GatewayResponse.model_validate_json(json_str)

        assert restored.payload == msgpack_like
