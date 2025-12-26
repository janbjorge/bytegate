"""Configuration for bytegate components."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BytegateConfig(BaseSettings):
    connections_hash: str = "bytegate:connections"
    tx_list_pattern: str = "bytegate:{connection_id}:tx"
    tx_processing_list_pattern: str = "bytegate:{connection_id}:tx:processing"
    rx_list_pattern: str = "bytegate:{connection_id}:rx"

    default_timeout_seconds: float = 30.0
    response_timeout_seconds: float = 30.0
    heartbeat_interval_seconds: int = 10

    model_config = SettingsConfigDict(env_prefix="bytegate_")

    def tx_list(self, connection_id: str) -> str:
        return self.tx_list_pattern.format(connection_id=connection_id)

    def tx_processing_list(self, connection_id: str) -> str:
        return self.tx_processing_list_pattern.format(connection_id=connection_id)

    def rx_list(self, connection_id: str) -> str:
        return self.rx_list_pattern.format(connection_id=connection_id)
