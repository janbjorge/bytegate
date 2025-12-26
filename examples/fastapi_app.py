"""Minimal FastAPI app wiring bytegate router and Redis."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis import asyncio as aioredis

from bytegate.router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.extra["redis"] = aioredis.from_url("redis://localhost:6379/0")
    app.extra["server_id"] = "local-dev"
    try:
        yield
    finally:
        await app.extra["redis"].close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
