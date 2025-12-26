"""Connect to bytegate and echo any incoming bytes."""

import argparse
import asyncio

import websockets


async def run(connection_id: str, host: str) -> None:
    url = f"{host}/bytegate/{connection_id}"
    async with websockets.connect(url) as websocket:
        print(f"connected: {url}")
        async for message in websocket:
            if isinstance(message, str):
                message = message.encode("utf-8")
            await websocket.send(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--host", default="ws://127.0.0.1:8000")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.connection_id, args.host))


if __name__ == "__main__":
    main()
