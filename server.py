import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from edi_triage import FailedTransaction, list_fixtures, run_edi_triage
from mcp_server import streamable_http_app
from rate_limit import RateLimitMiddleware

_mcp_app = streamable_http_app()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    async with _mcp_app.router.lifespan_context(_mcp_app):
        yield


app = FastAPI(title="EDI Failure Triage", lifespan=_lifespan)
app.mount("/mcp", _mcp_app)

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(RateLimitMiddleware)

UVICORN_PORT = int(os.getenv("PORT", "8001"))
BEDROCK_MOCK = os.getenv("BEDROCK_MOCK", "true").lower() == "true"
STATIC_INDEX = Path(__file__).with_name("static") / "index.html"


@app.post("/triage")
async def triage(tx: FailedTransaction):
    if not tx.id.strip():
        raise HTTPException(status_code=400, detail="id is required")

    return run_edi_triage(tx, mock=BEDROCK_MOCK)


@app.get("/fixtures")
async def fixtures():
    # Not under /triage on purpose: listing fixtures is free, so it stays
    # outside the rate limit that guards model calls.
    return list_fixtures()


@app.get("/")
async def root():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return HTMLResponse("<h1>EDI Failure Triage</h1><p>Demo page not found.</p>")


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "mock" if BEDROCK_MOCK else "bedrock"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=UVICORN_PORT)
