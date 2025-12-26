"""
Bytegate message models.

These models define the envelope format for gateway messages.
The `payload` field is raw bytes - the gateway does not inspect it.
"""

from base64 import b64decode, b64encode
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer, field_validator


def _generate_request_id() -> str:
    return uuid4().hex


class GatewayEnvelope(BaseModel):
    """
    Envelope for messages sent through the gateway.

    The gateway only inspects the envelope metadata (request_id, connection_id).
    The payload is passed through as-is without inspection or validation.

    When serialized to JSON, payload bytes are base64-encoded.
    """

    request_id: str = Field(default_factory=_generate_request_id)
    connection_id: str
    payload: bytes  # Opaque - the gateway does not inspect this

    @field_serializer("payload")
    def serialize_payload(self, value: bytes) -> str:
        """Encode bytes as base64 for JSON serialization."""
        return b64encode(value).decode("ascii")

    @field_validator("payload", mode="before")
    @classmethod
    def deserialize_payload(cls, value: str | bytes) -> bytes:
        """Decode base64 string back to bytes during parsing."""
        if isinstance(value, bytes):
            return value
        return b64decode(value)


class GatewayResponse(BaseModel):
    """
    Response envelope returned through the gateway.

    Like the request envelope, the payload is opaque bytes.
    """

    request_id: str
    payload: bytes  # Opaque - the gateway does not inspect this
    error: str | None = None  # Set if the remote system returned an error

    @field_serializer("payload")
    def serialize_payload(self, value: bytes) -> str:
        """Encode bytes as base64 for JSON serialization."""
        return b64encode(value).decode("ascii")

    @field_validator("payload", mode="before")
    @classmethod
    def deserialize_payload(cls, value: str | bytes) -> bytes:
        """Decode base64 string back to bytes during parsing."""
        if isinstance(value, bytes):
            return value
        return b64decode(value)
