import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import learning_routes
import main
from database import app_db


def _fake_client_with_sequence(texts: list[str]):
    queue = list(texts)

    def _generate_content(*args, **kwargs):
        if not queue:
            raise RuntimeError("No fake responses left")
        return SimpleNamespace(text=queue.pop(0))

    return SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))


@pytest.fixture(autouse=True)
def _reset_everything(monkeypatch: pytest.MonkeyPatch):
    main.rate_limit_store.clear()
    main.in_memory_cache.clear()
    learning_routes.DOCUMENTS.clear()
    learning_routes.USER_PROFILES.clear()
    app_db.clear_all()

    for key in list(main.METRICS.keys()):
        main.METRICS[key] = 0

    main.redis_client = None
    main.redis_disabled = True


@pytest.fixture
def client():
    return TestClient(main.app)


def _register_user(client: TestClient, user_id: str):
    response = client.post("/users/register", json={"user_id": user_id})
    assert response.status_code == 201


def _upload_txt(
    client: TestClient,
    user_id: str,
    text: str = "Neural networks learn patterns from data.",
):
    response = client.post(
        "/documents/upload",
        data={"user_id": user_id},
        files={"file": ("notes.txt", text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200
    return response.json()["document_id"]


def test_user_id_must_be_unique(client: TestClient):
    first = client.post("/users/register", json={"user_id": "user_1"})
    second = client.post("/users/register", json={"user_id": "user_1"})

    assert first.status_code == 201
    assert second.status_code == 409


def test_upload_and_list_documents(client: TestClient):
    _register_user(client, "user_1")
    document_id = _upload_txt(client, user_id="user_1")
    assert document_id

    listed = client.get("/documents", params={"user_id": "user_1"})
    assert listed.status_code == 200
    documents = listed.json()["documents"]
    assert len(documents) == 1
    assert documents[0]["document_id"] == document_id


def test_ask_document_returns_answer_and_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _register_user(client, "user_1")
    document_id = _upload_txt(client, user_id="user_1", text="Transformers use attention mechanisms in deep learning.")

    payload = json.dumps(
        {
            "answer": "Transformer models rely on attention to focus on relevant tokens.",
            "confidence": 0.84,
            "used_chunk_ids": ["c1"],
            "suggested_topics": ["attention", "transformer"],
        }
    )
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_sequence([payload]))

    response = client.post(
        "/documents/ask",
        json={
            "document_id": document_id,
            "user_id": "user_1",
            "question": "Transformer neden dikkat mekanizmasi kullanir?",
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "attention" in [topic.lower() for topic in data["suggested_topics"]]
    assert len(data["sources"]) >= 1


def test_quiz_flashcards_and_progress_flow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _register_user(client, "user_1")
    document_id = _upload_txt(
        client,
        user_id="user_1",
        text="API security uses authentication, rate limiting, and request validation for safer services.",
    )

    quiz_payload = json.dumps(
        {
            "title": "Security Quiz",
            "questions": [
                {
                    "question": "Which mechanism verifies user identity?",
                    "options": ["Caching", "Authentication", "Compression", "Sharding"],
                    "correct_answer": "Authentication",
                    "explanation": "Authentication checks identity.",
                    "topic": "authentication",
                },
                {
                    "question": "What helps prevent abuse by limiting requests?",
                    "options": ["Rate limiting", "Retry loops", "Threading", "Hashing"],
                    "correct_answer": "Rate limiting",
                    "explanation": "Rate limiting controls request volume.",
                    "topic": "rate limiting",
                },
                {
                    "question": "What ensures incoming data meets schema rules?",
                    "options": ["Validation", "Proxying", "Mirroring", "Batching"],
                    "correct_answer": "Validation",
                    "explanation": "Validation checks input structure.",
                    "topic": "validation",
                },
            ],
        }
    )

    flash_payload = json.dumps(
        {
            "title": "Security Cards",
            "cards": [
                {
                    "front": "Authentication",
                    "back": "Confirms who the user is.",
                    "topic": "authentication",
                },
                {
                    "front": "Rate limiting",
                    "back": "Limits requests over time.",
                    "topic": "rate limiting",
                },
                {
                    "front": "Validation",
                    "back": "Rejects malformed input.",
                    "topic": "validation",
                },
                {
                    "front": "Caching",
                    "back": "Stores repeated results.",
                    "topic": "caching",
                },
                {
                    "front": "API key",
                    "back": "Simple shared secret for access control.",
                    "topic": "authentication",
                },
            ],
        }
    )

    fake_client = _fake_client_with_sequence([quiz_payload, flash_payload])
    monkeypatch.setattr(main, "get_genai_client", lambda: fake_client)

    quiz = client.post(
        "/quiz/generate",
        json={
            "document_id": document_id,
            "user_id": "user_1",
            "question_count": 3,
            "difficulty": "medium",
            "focus_topics": ["authentication"],
        },
    )
    assert quiz.status_code == 200
    quiz_data = quiz.json()

    answers = [
        {"question_id": quiz_data["questions"][0]["id"], "selected_answer": "Authentication"},
        {"question_id": quiz_data["questions"][1]["id"], "selected_answer": "Retry loops"},
        {"question_id": quiz_data["questions"][2]["id"], "selected_answer": "Validation"},
    ]

    submitted = client.post(
        "/quiz/submit",
        json={
            "quiz_id": quiz_data["quiz_id"],
            "document_id": document_id,
            "user_id": "user_1",
            "questions": quiz_data["questions"],
            "answers": answers,
        },
    )
    assert submitted.status_code == 200
    submit_data = submitted.json()
    assert submit_data["score"] == 2
    assert "rate limiting" in submit_data["weak_topics"]

    flashcards = client.post(
        "/flashcards/generate",
        json={
            "document_id": document_id,
            "user_id": "user_1",
            "card_count": 5,
            "focus_topics": ["rate limiting"],
        },
    )
    assert flashcards.status_code == 200
    assert len(flashcards.json()["cards"]) >= 3

    review = client.post(
        "/flashcards/review",
        json={"user_id": "user_1", "topic": "rate limiting", "confidence": 2},
    )
    assert review.status_code == 200

    progress = client.get("/users/user_1/progress")
    assert progress.status_code == 200
    progress_data = progress.json()
    assert progress_data["quiz_attempts"] >= 1
    assert progress_data["answered_questions_total"] == 3
    assert any(item["topic"] == "rate limiting" for item in progress_data["weak_topics"])
