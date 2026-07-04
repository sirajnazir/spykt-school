"""FastAPI gateway — Phase 0 hello-world skeleton.

Real surfaces (01-TECH_SPEC §8) land in Phase 1+ behind typed contracts.
Phase 0 gate only requires a deployable service with a health check.
"""

import logging

from fastapi import FastAPI, Request

logger = logging.getLogger("spykt.api")

app = FastAPI(title="Spykt API", version="0.0.1")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "api"}


@app.get("/")
def root() -> dict:
    return {"service": "spykt-api", "phase": 0, "docs": "/docs"}


@app.post("/webhooks/clerk")
async def clerk_webhook(request: Request) -> dict:
    """Clerk user/org events → mirrored into students/families/coaches (01 §3).

    Phase 0 stub: accepts and acknowledges only. Signature verification (svix)
    and table mirroring are Phase 1 work — do NOT point production Clerk at
    this endpoint until then.
    """
    payload = await request.json()
    logger.info("clerk webhook received: type=%s", payload.get("type", "unknown"))
    return {"received": True}
