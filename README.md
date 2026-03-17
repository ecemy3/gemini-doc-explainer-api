# AI Document Explainer API

A production-ready FastAPI service that uses Google's Gemini 2.0 Flash model to explain complex documents at different expertise levels.

## Features

- 🤖 **Gemini 2.0 Flash Integration** - Powered by Google's latest language model via Vertex AI
- 📊 **Structured JSON Output** - Strict schema validation using Pydantic models
- 🔒 **Rate Limiting** - IP-based rate limiting (10 requests/minute per IP)
- ⚡ **Redis-Backed Caching** - Distributed cache with in-memory fallback
- 💰 **Cost Metrics** - Estimated token and request cost tracking
- 📄 **Document Upload & Parsing** - Supports `.txt`, `.pdf`, `.docx`
- ❓ **Document Q&A (RAG)** - Answers with source snippets from uploaded documents
- 🧠 **Quiz + Flashcards** - Auto-generates study quizzes and flashcards
- 📈 **Adaptive Learning** - Tracks weak topics from user questions and quiz outcomes
- 🗄️ **SQLite Persistence** - Users, documents, Q&A, quizzes, flashcards, and progress are saved
- 🆔 **Unique User IDs** - Duplicate user IDs are rejected during registration
- 🖥️ **Built-in Web UI** - Interactive learning interface at `/app`
- 📝 **Structured Logging** - JSON-formatted logs with request tracking and latency metrics
- 🆔 **Request Tracking** - Unique request IDs for debugging and monitoring
- 🐳 **Docker Ready** - Containerized for easy deployment
- ☁️ **Cloud Run Optimized** - Ready for deployment on Google Cloud Run

## API Endpoints

### `POST /explain`

Explains the provided text at the specified expertise level.

**Request Body:**
```json
{
  "text": "Your text to be explained",
  "level": "beginner"
}
```

**Explanation Levels:**
- `beginner` - Simple, jargon-free explanations
- `intermediate` - Balanced technical depth
- `expert` - Advanced technical details

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "level": "beginner",
  "summary": "High-level explanation of the text",
  "key_points": [
    "Key takeaway 1",
    "Key takeaway 2",
    "Key takeaway 3"
  ],
  "warnings": [],
  "confidence": 0.95
}
```

### `GET /health`

Health check endpoint.

**Response:**
```json
{
  "status": "ok"
}
```

### `GET /metrics`

Returns runtime metrics (request counts, cache stats, estimated token/cost totals).

**Response (example):**
```json
{
  "requests_total": 15,
  "success_total": 14,
  "rate_limited_total": 1,
  "cache_hits": 6,
  "cache_misses": 8,
  "upstream_calls_total": 8,
  "estimated_total_cost_usd": 0.0021,
  "cache_backend": "redis",
  "rate_limit_backend": "redis"
}
```

### `GET /`

Root endpoint with service information.

### Learning Endpoints

- `POST /users/register` - Register a unique user ID (duplicates return `409`)
- `POST /documents/upload` - Upload `.txt`, `.pdf`, `.docx` documents (`user_id` required)
- `GET /documents` - List uploaded documents
- `POST /documents/ask` - Ask questions grounded in a selected document
- `POST /quiz/generate` - Generate quiz questions from a document
- `POST /quiz/submit` - Submit quiz answers and update weak topics
- `POST /flashcards/generate` - Generate study cards from a document
- `POST /flashcards/review` - Record confidence feedback per card/topic
- `GET /users/{user_id}/progress` - Get adaptive progress and recommendations

### Admin Endpoints

- `GET /admin/db/overview` - Table counts + latest rows preview
- `GET /admin/db/table/{table_name}` - Query rows from a specific table

## Installation

### Prerequisites

- Python 3.11+
- Google Cloud Project with Vertex AI API enabled
- Service account with Vertex AI permissions

### Local Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ecemy3/gemini-doc-explainer-api.git
   cd gemini-doc-explainer-api
   ```

2. **Create and activate virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set environment variables:**
   ```bash
   export GCP_PROJECT="your-project-id"
   export GCP_LOCATION="europe-west1"
   export GEMINI_MODEL="gemini-2.0-flash"
  export GEMINI_MODELS="gemini-2.0-flash,gemini-2.0-flash-001,gemini-2.5-flash"
  # Optional: if you use Gemini API key instead of Vertex IAM
  export GOOGLE_API_KEY="your-google-ai-api-key"
  export REDIS_URL="redis://localhost:6379/0"
   ```

5. **Authenticate with Google Cloud:**
   ```bash
   gcloud auth application-default login
   ```

6. **Run the application:**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8080
   ```

7. **Access the API:**
   - API Documentation: http://localhost:8080/docs
   - Health Check: http://localhost:8080/health
  - Learning UI: http://localhost:8080/app

## Docker Deployment

### Build and Run Locally

```bash
docker build -t ai-doc-explainer .
docker run -p 8080:8080 \
  -e GCP_PROJECT="your-project-id" \
  -e GCP_LOCATION="europe-west1" \
  -e REDIS_URL="redis://host.docker.internal:6379/0" \
  -e GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json" \
  -v /path/to/service-account.json:/path/to/service-account.json:ro \
  ai-doc-explainer
```

## Cloud Run Deployment

### Deploy to Google Cloud Run

```bash
# Set environment variables
export PROJECT_ID="your-project-id"
export REGION="europe-west1"
export SERVICE_NAME="ai-doc-explainer"

# Build and push to Artifact Registry
gcloud builds submit --tag gcr.io/${PROJECT_ID}/${SERVICE_NAME}

# Deploy to Cloud Run
gcloud run deploy ${SERVICE_NAME} \
  --image gcr.io/${PROJECT_ID}/${SERVICE_NAME} \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT=${PROJECT_ID},GCP_LOCATION=${REGION},GEMINI_MODEL=gemini-2.0-flash,REDIS_URL=redis://your-redis-host:6379/0 \
  --memory 512Mi \
  --timeout 30s \
  --max-instances 10
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GCP_PROJECT` | `ai-doc-explainer` | Google Cloud project ID |
| `GCP_LOCATION` | `europe-west1` | Vertex AI location |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model to use |
| `GEMINI_MODELS` | `GEMINI_MODEL` | Comma-separated fallback model list |
| `GOOGLE_API_KEY` | `` | Use Gemini API key mode instead of Vertex IAM |
| `DATABASE_PATH` | `ai_doc_explainer.db` | SQLite database file path |
| `REDIS_URL` | `` | Redis connection URL for distributed cache/rate limit |
| `CACHE_TTL_SEC` | `300` | Cache duration in seconds |
| `INPUT_COST_PER_1M_TOKENS` | `0` | Input token price for cost estimation |
| `OUTPUT_COST_PER_1M_TOKENS` | `0` | Output token price for cost estimation |
| `PORT` | `8080` | Server port (auto-set by Cloud Run) |

If you get `404 NOT_FOUND` model errors on Vertex, set `GOOGLE_API_KEY` or update `GEMINI_MODELS` to models available in your region/project.

## Supported File Types

- `.txt` (UTF-8 text)
- `.pdf` (text-based PDF)
- `.docx` (Microsoft Word)

Maximum upload size: **10MB**

## Rate Limiting

The API implements IP-based rate limiting:
- **Window:** 60 seconds
- **Max Requests:** 10 per IP
- **Response:** HTTP 429 when exceeded

When `REDIS_URL` is set, rate limiting is shared across all instances.
Without Redis, the service falls back to in-memory rate limiting.

To modify rate limits, update these constants in `main.py`:
```python
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 10
```

## Monitoring & Logging

All requests are logged in JSON format with the following fields:

**Success Log:**
```json
{
  "event": "explain_success",
  "request_id": "uuid",
  "level": "beginner",
  "input_length": 1234,
  "model": "gemini-2.0-flash",
  "latency_ms": 850
}
```

**Failure Log:**
```json
{
  "event": "explain_failure",
  "request_id": "uuid",
  "error": "error message"
}
```

### Cloud Run Logs Queries

```sql
-- Average latency
jsonPayload.latency_ms

-- Slow requests (>2s)
jsonPayload.latency_ms > 2000

-- Failed requests
jsonPayload.event = "explain_failure"
```

## Example Usage

### cURL

Register user first:

```bash
curl -X POST "http://localhost:8080/users/register" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"ecemm_unique_1"}'
```

Upload document for that user:

```bash
curl -X POST "http://localhost:8080/documents/upload" \
  -F "user_id=ecemm_unique_1" \
  -F "file=@./notes.pdf"
```

Ask explain endpoint:

```bash
curl -X POST "http://localhost:8080/explain" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed.",
    "level": "beginner"
  }'
```

Admin DB overview:

```bash
curl "http://localhost:8080/admin/db/overview?limit=5"
```

Admin DB table sample:

```bash
curl "http://localhost:8080/admin/db/table/users?limit=20"
```

### Python

```python
import requests

response = requests.post(
    "http://localhost:8080/explain",
    json={
        "text": "Your text here",
        "level": "intermediate"
    }
)

print(response.json())
```

### JavaScript

```javascript
const response = await fetch('http://localhost:8080/explain', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    text: 'Your text here',
    level: 'expert'
  })
});

const data = await response.json();
console.log(data);
```

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── ci.yml        # GitHub Actions CI pipeline
├── tests/
│   ├── test_api.py       # Core API tests
│   └── test_learning_api.py  # Document learning flow tests
├── static/
│   ├── index.html        # Web interface
│   ├── styles.css        # UI styles
│   └── app.js            # UI logic
├── database.py           # SQLite persistence layer
├── learning_routes.py    # Document Q&A, quiz, flashcard, progress APIs
├── main.py              # FastAPI application
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container configuration
├── test_gemini.py      # Manual Gemini smoke script
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Testing

Run the automated test suite locally:

```bash
pytest -q
```

CI also runs this test suite automatically on each push and pull request via GitHub Actions.

## Error Handling

| Status Code | Description |
|-------------|-------------|
| `200` | Success |
| `422` | Validation error (invalid input) |
| `429` | Rate limit exceeded |
| `502` | Upstream Gemini API error |

## Security Considerations

- ✅ Environment variables for sensitive data
- ✅ Rate limiting to prevent abuse
- ✅ Input validation (max 20,000 characters)
- ✅ Redis support for distributed rate limiting and caching

## Performance

- **Average Latency:** ~800ms (depends on text length and model load)
- **Max Input:** 20,000 characters
- **Max Output:** 500 tokens
- **Timeout:** 30 seconds (Cloud Run default)

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License.

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Powered by [Google Gemini 2.0 Flash](https://ai.google.dev/)
- Deployed on [Google Cloud Run](https://cloud.google.com/run)

## Support

For issues and questions, please open an issue on [GitHub](https://github.com/ecemy3/gemini-doc-explainer-api/issues).
