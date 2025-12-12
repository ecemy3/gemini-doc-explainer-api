# AI Document Explainer API

A production-ready FastAPI service that uses Google's Gemini 2.0 Flash model to explain complex documents at different expertise levels.

## Features

-  **Gemini 2.0 Flash Integration** - Powered by Google's latest language model via Vertex AI
-  **Structured JSON Output** - Strict schema validation using Pydantic models
-  **Rate Limiting** - IP-based rate limiting (10 requests/minute per IP)
-  **Structured Logging** - JSON-formatted logs with request tracking and latency metrics
-  **Request Tracking** - Unique request IDs for debugging and monitoring
-  **Docker Ready** - Containerized for easy deployment
-  **Cloud Run Optimized** - Ready for deployment on Google Cloud Run

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

### `GET /`

Root endpoint with service information.

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

## Docker Deployment

### Build and Run Locally

```bash
docker build -t ai-doc-explainer .
docker run -p 8080:8080 \
  -e GCP_PROJECT="your-project-id" \
  -e GCP_LOCATION="europe-west1" \
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
  --set-env-vars GCP_PROJECT=${PROJECT_ID},GCP_LOCATION=${REGION},GEMINI_MODEL=gemini-2.0-flash \
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
| `PORT` | `8080` | Server port (auto-set by Cloud Run) |

## Rate Limiting

The API implements IP-based rate limiting:
- **Window:** 60 seconds
- **Max Requests:** 10 per IP
- **Response:** HTTP 429 when exceeded

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

```bash
curl -X POST "http://localhost:8080/explain" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed.",
    "level": "beginner"
  }'
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
├── main.py              # FastAPI application
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container configuration
├── test_gemini.py      # Test script
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Error Handling

| Status Code | Description |
|-------------|-------------|
| `200` | Success |
| `422` | Validation error (invalid input) |
| `429` | Rate limit exceeded |
| `502` | Upstream Gemini API error |

## Security Considerations

-  Environment variables for sensitive data
-  No API keys in code
-  Rate limiting to prevent abuse
-  Input validation (max 20,000 characters)
-  In-memory rate limiting (consider Redis for production scale)

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
