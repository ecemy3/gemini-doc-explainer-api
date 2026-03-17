import hashlib
import json
import logging
import math
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field

from database import app_db
from learning_routes import create_learning_router

try:
    import redis
except Exception:
    redis = None

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
GEMINI_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_MODELS",
        f"{GEMINI_MODEL},gemini-2.0-flash-001,gemini-2.5-flash,gemini-1.5-flash",
    ).split(",")
    if model.strip()
]
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", os.getenv("GEMINI_API_KEY", "")).strip()

REDIS_URL = os.getenv("REDIS_URL", "")
CACHE_TTL_SEC = int(os.getenv("CACHE_TTL_SEC", "300"))

INPUT_COST_PER_1M_TOKENS = float(os.getenv("INPUT_COST_PER_1M_TOKENS", "0"))
OUTPUT_COST_PER_1M_TOKENS = float(os.getenv("OUTPUT_COST_PER_1M_TOKENS", "0"))


# =========================================================
# Rate limiting (in-memory, per IP)
# =========================================================

RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 10

rate_limit_store: dict[str, list[float]] = defaultdict(list)
in_memory_cache: dict[str, tuple[float, str]] = {}


METRICS: dict[str, float] = {
    "requests_total": 0,
    "success_total": 0,
    "unauthorized_total": 0,
    "rate_limited_total": 0,
    "upstream_calls_total": 0,
    "upstream_failures_total": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "input_tokens_total": 0,
    "output_tokens_total": 0,
    "estimated_total_cost_usd": 0,
    "documents_uploaded_total": 0,
    "qa_requests_total": 0,
    "quizzes_generated_total": 0,
    "quiz_submissions_total": 0,
    "flashcards_generated_total": 0,
    "flashcard_reviews_total": 0,
}


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup() -> None:
    app_db.init()


# =========================================================
# Gemini Client (Vertex AI via IAM)
# =========================================================

client = None
redis_client: Any = None
redis_disabled = False


def get_genai_client():
    global client
    if client is None:
        if GOOGLE_API_KEY:
            client = genai.Client(api_key=GOOGLE_API_KEY)
            logger.info(json.dumps({"event": "genai_client_initialized", "mode": "api_key"}))
        else:
            client = genai.Client(
                vertexai=True,
                project=GCP_PROJECT,
                location=GCP_LOCATION,
            )
            logger.info(json.dumps({"event": "genai_client_initialized", "mode": "vertex_ai"}))
    return client


def generate_content_with_fallback(
    contents: str,
    max_output_tokens: int,
    response_mime_type: str | None = None,
    temperature: float = 0.2,
) -> tuple[Any, str]:
    genai_client = get_genai_client()
    errors: list[str] = []

    for model_name in GEMINI_MODELS:
        try:
            increment_metric("upstream_calls_total")
            config_kwargs: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
            if response_mime_type:
                config_kwargs["response_mime_type"] = response_mime_type

            response = genai_client.models.generate_content(
                model=model_name,
                contents=contents,
                config=GenerateContentConfig(**config_kwargs),
            )
            return response, model_name
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")

    attempted_models = ", ".join(GEMINI_MODELS)
    last_error = errors[-1] if errors else "unknown error"
    raise RuntimeError(
        f"Gemini upstream error after trying models [{attempted_models}]. "
        f"Last error: {last_error}. Set GOOGLE_API_KEY for Gemini API access or configure GEMINI_MODELS."
    )


def get_redis_client():
    global redis_client
    global redis_disabled

    if redis_disabled:
        return None

    if redis_client is not None:
        return redis_client

    if not REDIS_URL or redis is None:
        redis_disabled = True
        return None

    try:
        redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        logger.info(json.dumps({"event": "redis_connected"}))
        return redis_client
    except Exception as e:
        redis_disabled = True
        logger.warning(json.dumps({"event": "redis_unavailable", "error": str(e)}))
        return None


def increment_metric(name: str, amount: float = 1) -> None:
    METRICS[name] = METRICS.get(name, 0) + amount


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def estimate_request_cost_usd(input_tokens: int, output_tokens: int) -> float:
    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_1M_TOKENS
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_1M_TOKENS
    return input_cost + output_cost


def build_cache_key(level: str, text: str) -> str:
    payload = f"{level}:{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_cached_response(cache_key: str) -> dict | None:
    redis_conn = get_redis_client()
    if redis_conn is not None:
        try:
            raw = redis_conn.get(f"cache:{cache_key}")
            if raw:
                return json.loads(raw)
            return None
        except Exception as e:
            logger.warning(json.dumps({"event": "cache_read_failed", "error": str(e)}))

    now = time.time()
    cached = in_memory_cache.get(cache_key)
    if not cached:
        return None

    expires_at, payload = cached
    if expires_at <= now:
        del in_memory_cache[cache_key]
        return None

    return json.loads(payload)


def set_cached_response(cache_key: str, payload: dict) -> None:
    serialized = json.dumps(payload)
    redis_conn = get_redis_client()
    if redis_conn is not None:
        try:
            redis_conn.setex(f"cache:{cache_key}", CACHE_TTL_SEC, serialized)
            return
        except Exception as e:
            logger.warning(json.dumps({"event": "cache_write_failed", "error": str(e)}))

    in_memory_cache[cache_key] = (time.time() + CACHE_TTL_SEC, serialized)


def is_rate_limited(client_ip: str, now: float) -> bool:
    redis_conn = get_redis_client()
    if redis_conn is not None:
        key = f"rate:{client_ip}"
        window_start = now - RATE_LIMIT_WINDOW_SEC
        member = f"{now}-{uuid.uuid4()}"
        try:
            pipe = redis_conn.pipeline()
            pipe.zremrangebyscore(key, "-inf", window_start)
            pipe.zcard(key)
            _, request_count = pipe.execute()

            if int(request_count) >= RATE_LIMIT_MAX_REQUESTS:
                return True

            pipe = redis_conn.pipeline()
            pipe.zadd(key, {member: now})
            pipe.expire(key, RATE_LIMIT_WINDOW_SEC)
            pipe.execute()
            return False
        except Exception as e:
            logger.warning(json.dumps({"event": "rate_limit_redis_failed", "error": str(e)}))

    timestamps = rate_limit_store[client_ip]
    rate_limit_store[client_ip] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SEC
    ]

    if len(rate_limit_store[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return True

    rate_limit_store[client_ip].append(now)
    return False


# =========================================================
# System Endpoints
# =========================================================

@app.get("/")
def root():
    return {"message": "Service is running. Go to /docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/app")
def learning_ui():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.get("/metrics")
def metrics():
    backend = "redis" if get_redis_client() is not None else "memory"
    data = dict(METRICS)
    data["cache_backend"] = backend
    data["rate_limit_backend"] = backend
    return data


@app.get("/admin/db/overview")
def admin_db_overview(limit: int = Query(default=5, ge=1, le=50)):
    counts = app_db.get_table_counts()
    preview = {
        "users": app_db.get_table_rows("users", limit),
        "documents": app_db.get_table_rows("documents", limit),
        "qa_logs": app_db.get_table_rows("qa_logs", limit),
        "quizzes": app_db.get_table_rows("quizzes", limit),
        "quiz_submissions": app_db.get_table_rows("quiz_submissions", limit),
        "flashcard_decks": app_db.get_table_rows("flashcard_decks", limit),
        "flashcard_reviews": app_db.get_table_rows("flashcard_reviews", limit),
    }
    return {
        "database_path": str(app_db.db_path),
        "counts": counts,
        "preview": preview,
    }


@app.get("/admin/db/table/{table_name}")
def admin_db_table(
    table_name: str,
    limit: int = Query(default=20, ge=1, le=200),
):
    try:
        rows = app_db.get_table_rows(table_name, limit)
        counts = app_db.get_table_counts()
        return {
            "table": table_name,
            "count": counts.get(table_name, 0),
            "rows": rows,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


app.include_router(
    create_learning_router(
        get_genai_client=lambda: get_genai_client(),
        increment_metric=lambda name, amount=1: increment_metric(name, amount),
        gemini_models=GEMINI_MODELS,
    )
)


# =========================================================
# Core Endpoint
# =========================================================

@app.post("/explain", response_model=ExplainResponse)
def explain(
    req: ExplainRequest,
    request: Request,
) -> ExplainResponse:
    """
    Explain text using Gemini with STRICT structured JSON output.
    """
    increment_metric("requests_total")

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    request_id = str(uuid.uuid4())
    start = time.time()

    if is_rate_limited(client_ip, now):
        increment_metric("rate_limited_total")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
        )

    cache_key = build_cache_key(req.level, req.text)
    cached_payload = get_cached_response(cache_key)
    if cached_payload is not None:
        increment_metric("cache_hits")
        increment_metric("success_total")
        cached_payload["request_id"] = request_id
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            json.dumps(
                {
                    "event": "explain_success",
                    "request_id": request_id,
                    "level": req.level,
                    "input_length": len(req.text),
                    "model": GEMINI_MODELS[0],
                    "latency_ms": elapsed_ms,
                    "cache_hit": True,
                }
            )
        )
        return ExplainResponse(**cached_payload)

    increment_metric("cache_misses")

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
        response, used_model = generate_content_with_fallback(
            contents=prompt,
            max_output_tokens=500,
            response_mime_type="application/json",
        )
    except Exception as e:
        increment_metric("upstream_failures_total")
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
        increment_metric("upstream_failures_total")
        raise HTTPException(
            status_code=502,
            detail="Gemini returned empty response",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        increment_metric("upstream_failures_total")
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
        cached_payload = {
            "level": data["level"],
            "summary": data["summary"],
            "key_points": data["key_points"],
            "warnings": data["warnings"],
            "confidence": data["confidence"],
        }
        set_cached_response(cache_key, cached_payload)

        data["request_id"] = request_id
        result = ExplainResponse(**data)

        input_tokens = estimate_tokens(req.text)
        output_tokens = estimate_tokens(raw)
        request_cost = estimate_request_cost_usd(input_tokens, output_tokens)

        increment_metric("input_tokens_total", input_tokens)
        increment_metric("output_tokens_total", output_tokens)
        increment_metric("estimated_total_cost_usd", request_cost)
        increment_metric("success_total")

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            json.dumps({
                "event": "explain_success",
                "request_id": request_id,
                "level": req.level,
                "input_length": len(req.text),
                "model": used_model,
                "latency_ms": elapsed_ms,
                "cache_hit": False,
                "estimated_input_tokens": input_tokens,
                "estimated_output_tokens": output_tokens,
                "estimated_cost_usd": request_cost,
            })
        )

        return result
    except Exception as e:
        increment_metric("upstream_failures_total")
        logger.error(json.dumps({
            "event": "explain_failure",
            "request_id": request_id,
            "error": str(e),
        }))
        raise HTTPException(
            status_code=502,
            detail=f"Gemini JSON schema mismatch: {e}",
        )
