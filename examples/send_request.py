"""Send a request through bytegate and print the response."""

import argparse
import asyncio

from redis import asyncio as aioredis

from bytegate import GatewayClient


async def run(connection_id: str, payload: bytes, redis_url: str) -> None:
    redis = aioredis.from_url(redis_url)
    client = GatewayClient(redis)

    response = await client.send(connection_id, payload)
    print(response.payload.decode("utf-8", errors="replace"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--payload", default="ping")
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.connection_id, args.payload.encode("utf-8"), args.redis_url))


if __name__ == "__main__":
    main()
