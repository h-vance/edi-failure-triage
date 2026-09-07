import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import demo_journey
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

    result = run_edi_triage(tx, mock=BEDROCK_MOCK)
    # The console's Ticket flow tab reads this back: n8n calls /triage, so the server
    # knows exactly when the triage step happened without asking n8n for node data.
    demo_journey.TRIAGE_SEEN[tx.id] = {"at": time.time(), "classification": result["classification"]}
    return result


class DemoTicket(BaseModel):
    fixture: str


@app.get("/demo/status")
async def demo_status():
    return demo_journey.status()


@app.post("/demo/ticket")
async def demo_ticket(body: DemoTicket):
    st = demo_journey.status()
    if not st["ready"]:
        raise HTTPException(status_code=503, detail="missing in .env: " + ", ".join(st["missing"]))
    try:
        return demo_journey.start(body.fixture)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown fixture {body.fixture}")
    except (OSError, RuntimeError) as e:  # Intercom or n8n unreachable: say so, keep the console alive
        raise HTTPException(status_code=502, detail=str(e)[:300])


@app.get("/demo/ticket/{conversation_id}")
async def demo_ticket_poll(conversation_id: str):
    if conversation_id not in demo_journey.JOURNEYS:
        raise HTTPException(status_code=404, detail="unknown conversation")
    try:
        return demo_journey.poll(conversation_id)
    except (OSError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e)[:300])


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
