import time
import uuid
import logging

import json
import os
from typing import List, Literal
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from google import genai
from google.genai.types import GenerateContentConfig


# =========================================================
# Logger
# =========================================================

logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO)


# =========================================================
# Types
# =========================================================

ExplanationLevel = Literal["beginner", "intermediate", "expert"]


# =========================================================
# Request / Response Models
# =========================================================

class ExplainRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Text to be explained",
    )
    level: ExplanationLevel = Field(
        ...,
        description="Explanation depth",
    )


class ExplainResponse(BaseModel):
    request_id: str
    level: ExplanationLevel
    summary: str
    key_points: List[str]
    warnings: List[str]
    confidence: float


# =========================================================
# App
# =========================================================

app = FastAPI(
    title="AI Document Explainer API",
    version="0.3.0",
    description="Gemini-powered document explanation service",
)


# =========================================================
# Config
# =========================================================

GCP_PROJECT = os.getenv("GCP_PROJECT", "ai-doc-explainer")
GCP_LOCATION = os.getenv("GCP_LOCATION", "europe-west1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


# =========================================================
# Rate limiting (in-memory, per IP)
# =========================================================

RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 10

rate_limit_store: dict[str, list[float]] = defaultdict(list)


# =========================================================
# Gemini Client (Vertex AI via IAM)
# =========================================================

client = genai.Client(
    vertexai=True,
    project=GCP_PROJECT,
    location=GCP_LOCATION,
)


# =========================================================
# System Endpoints
# =========================================================

@app.get("/")
def root():
    return {"message": "Service is running. Go to /docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# =========================================================
# Core Endpoint
# =========================================================

@app.post("/explain", response_model=ExplainResponse)
def explain(req: ExplainRequest, request: Request) -> ExplainResponse:
    """
    Explain text using Gemini with STRICT structured JSON output.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    timestamps = rate_limit_store[client_ip]

    # keep only requests in the last window
    rate_limit_store[client_ip] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SEC
    ]

    if len(rate_limit_store[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
        )

    rate_limit_store[client_ip].append(now)
    request_id = str(uuid.uuid4())
    start = time.time()

    prompt = f"""
You are a senior technical writer.

Explain the following text at "{req.level}" level.

You MUST return ONLY valid JSON with EXACTLY this structure:

{{
  "level": "{req.level}",
  "summary": "short explanation",
  "key_points": ["point 1", "point 2", "point 3"],
  "warnings": [],
  "confidence": 0.0
}}

Rules:
- key_points MUST have 3–6 items
- warnings MUST be an array (empty if none)
- confidence MUST be a float between 0 and 1
- DO NOT include markdown
- DO NOT include extra text

Text:
{req.text}
""".strip()

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=500,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        logger.error(json.dumps({
            "event": "explain_failure",
            "request_id": request_id,
            "error": str(e),
        }))
        raise HTTPException(
            status_code=502,
            detail=f"Gemini upstream error: {e}",
        )

    raw = (response.text or "").strip()
    if not raw:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned empty response",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(json.dumps({
            "event": "explain_failure",
            "request_id": request_id,
            "error": str(e),
        }))
        raise HTTPException(
            status_code=502,
            detail=f"Invalid JSON from Gemini: {e}",
        )

    # Validate against Pydantic model
    try:
        data["request_id"] = request_id
        result = ExplainResponse(**data)
        
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            json.dumps({
                "event": "explain_success",
                "request_id": request_id,
                "level": req.level,
                "input_length": len(req.text),
                "model": GEMINI_MODEL,
                "latency_ms": elapsed_ms,
            })
        )
        
        return result
    except Exception as e:
        logger.error(json.dumps({
            "event": "explain_failure",
            "request_id": request_id,
            "error": str(e),
        }))
        raise HTTPException(
            status_code=502,
            detail=f"Gemini JSON schema mismatch: {e}",
        )
