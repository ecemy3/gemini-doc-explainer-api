import io
import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field

from database import app_db

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except Exception:
    DocxDocument = None


MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120

DOCUMENTS: dict[str, dict[str, Any]] = {}
USER_PROFILES: dict[str, dict[str, Any]] = {}

STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "into",
    "about",
    "what",
    "when",
    "where",
    "which",
    "how",
    "why",
    "bir",
    "ve",
    "ile",
    "icin",
    "ama",
    "gibi",
    "daha",
    "olan",
    "neden",
    "nasil",
    "hangi",
    "soru",
}


class DocumentUploadResponse(BaseModel):
    document_id: str
    user_id: str
    filename: str
    chunk_count: int
    char_count: int


class RegisterUserRequest(BaseModel):
    user_id: str = Field(..., min_length=3, max_length=120)


class RegisterUserResponse(BaseModel):
    user_id: str
    created: bool


class AskDocumentRequest(BaseModel):
    document_id: str
    user_id: str = Field(default="anonymous", min_length=1, max_length=120)
    question: str = Field(..., min_length=3, max_length=5000)
    top_k: int = Field(default=4, ge=2, le=8)


class AskSource(BaseModel):
    chunk_id: str
    source: str
    excerpt: str


class AskDocumentResponse(BaseModel):
    request_id: str
    answer: str
    confidence: float
    sources: list[AskSource]
    suggested_topics: list[str]


QuizDifficulty = Literal["easy", "medium", "hard"]


class QuizQuestion(BaseModel):
    id: str
    question: str
    options: list[str]
    correct_answer: str
    explanation: str
    topic: str


class GenerateQuizRequest(BaseModel):
    document_id: str
    user_id: str = Field(default="anonymous", min_length=1, max_length=120)
    question_count: int = Field(default=5, ge=3, le=12)
    difficulty: QuizDifficulty = "medium"
    focus_topics: list[str] = Field(default_factory=list)


class GenerateQuizResponse(BaseModel):
    quiz_id: str
    title: str
    difficulty: QuizDifficulty
    focus_topics: list[str]
    questions: list[QuizQuestion]


class QuizAnswer(BaseModel):
    question_id: str
    selected_answer: str


class SubmitQuizRequest(BaseModel):
    quiz_id: str | None = None
    document_id: str
    user_id: str
    questions: list[QuizQuestion]
    answers: list[QuizAnswer]


class QuizQuestionResult(BaseModel):
    question_id: str
    topic: str
    selected_answer: str
    correct_answer: str
    is_correct: bool


class SubmitQuizResponse(BaseModel):
    score: int
    total: int
    accuracy: float
    weak_topics: list[str]
    recommended_topics: list[str]
    results: list[QuizQuestionResult]


class GenerateFlashcardsRequest(BaseModel):
    document_id: str
    user_id: str = Field(default="anonymous", min_length=1, max_length=120)
    card_count: int = Field(default=12, ge=5, le=30)
    focus_topics: list[str] = Field(default_factory=list)


class Flashcard(BaseModel):
    id: str
    front: str
    back: str
    topic: str


class GenerateFlashcardsResponse(BaseModel):
    deck_id: str
    title: str
    cards: list[Flashcard]


class ReviewFlashcardRequest(BaseModel):
    user_id: str
    topic: str
    confidence: int = Field(..., ge=1, le=5)


class TopicStat(BaseModel):
    topic: str
    count: int


class UserProgressResponse(BaseModel):
    user_id: str
    quiz_attempts: int
    answered_questions_total: int
    correct_answers_total: int
    accuracy: float
    asked_topics: list[TopicStat]
    weak_topics: list[TopicStat]
    recommendations: list[str]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]{3,}", text.lower()))


def _extract_keywords(text: str, limit: int = 6) -> list[str]:
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]{3,}", text.lower()) if t not in STOPWORDS]
    if not tokens:
        return []
    counts = Counter(tokens)
    return [token for token, _ in counts.most_common(limit)]


def _split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    clean = _normalize_whitespace(text)
    if not clean:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(clean)

    while start < text_len:
        end = min(text_len, start + chunk_size)
        chunk = clean[start:end].strip()
        if len(chunk) >= 20:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(0, end - overlap)

    return chunks


def _get_or_create_profile(user_id: str) -> dict[str, Any]:
    if user_id not in USER_PROFILES:
        USER_PROFILES[user_id] = app_db.get_user_profile_snapshot(user_id)
    return USER_PROFILES[user_id]


def _ensure_user_exists(user_id: str) -> None:
    if not app_db.user_exists(user_id):
        raise HTTPException(status_code=404, detail="User not found. Register user_id first.")


def _derive_focus_topics(profile: dict[str, Any], requested_topics: list[str]) -> list[str]:
    if requested_topics:
        return [topic.strip().lower() for topic in requested_topics if topic.strip()][:6]

    weak = [topic for topic, _ in profile["weak_topics"].most_common(4)]
    asked = [topic for topic, _ in profile["asked_topics"].most_common(3)]
    combined: list[str] = []
    for topic in weak + asked:
        if topic not in combined:
            combined.append(topic)
    return combined[:6]


def _parse_document(filename: str, content: bytes) -> list[tuple[str, str]]:
    suffix = Path(filename).suffix.lower()

    if suffix == ".txt":
        text = content.decode("utf-8", errors="ignore")
        return [("txt-1", text)]

    if suffix == ".pdf":
        if PdfReader is None:
            raise HTTPException(status_code=400, detail="PDF support is unavailable. Install pypdf.")
        reader = PdfReader(io.BytesIO(content))
        segments: list[tuple[str, str]] = []
        for idx, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                segments.append((f"page-{idx}", page_text))
        return segments

    if suffix == ".docx":
        if DocxDocument is None:
            raise HTTPException(status_code=400, detail="DOCX support is unavailable. Install python-docx.")
        document = DocxDocument(io.BytesIO(content))
        text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
        return [("docx-1", text)]

    raise HTTPException(status_code=415, detail="Unsupported file type. Use .txt, .pdf, or .docx")


def _build_document_chunks(filename: str, segments: list[tuple[str, str]]) -> tuple[list[dict[str, Any]], int]:
    chunks: list[dict[str, Any]] = []
    chunk_index = 1
    total_chars = 0

    for source_label, segment_text in segments:
        segment_text = _normalize_whitespace(segment_text)
        if not segment_text:
            continue

        total_chars += len(segment_text)
        for chunk_text in _split_text_into_chunks(segment_text):
            chunk_id = f"c{chunk_index}"
            chunk_index += 1
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "source": f"{filename}:{source_label}",
                    "text": chunk_text,
                    "terms": _tokenize(chunk_text),
                }
            )

    return chunks, total_chars


def _get_document_or_404(document_id: str) -> dict[str, Any]:
    document = DOCUMENTS.get(document_id)
    if document is None:
        document = app_db.get_document(document_id)
        if document is not None:
            DOCUMENTS[document_id] = document
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _rank_chunks(chunks: list[dict[str, Any]], query: str, top_k: int) -> list[dict[str, Any]]:
    query_terms = _tokenize(query)
    if not query_terms:
        return chunks[:top_k]

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, chunk in enumerate(chunks):
        overlap = len(query_terms.intersection(chunk["terms"]))
        phrase_bonus = 1 if query.lower()[:60] in chunk["text"].lower() else 0
        score = overlap * 2 + phrase_bonus
        scored.append((score, -idx, chunk))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = [item[2] for item in scored[:top_k] if item[0] > 0]
    if top:
        return top

    return chunks[:top_k]


def _safe_string_list(value: Any, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                items.append(normalized)
        if len(items) >= max_items:
            break
    return items


def _call_gemini_json(
    prompt: str,
    models: list[str],
    get_genai_client: Callable[[], Any],
    increment_metric: Callable[[str, float], None],
    max_output_tokens: int = 900,
) -> dict[str, Any]:
    genai_client = get_genai_client()
    errors: list[str] = []

    for model_name in models:
        try:
            increment_metric("upstream_calls_total")
            response = genai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                ),
            )

            raw = (response.text or "").strip()
            if not raw:
                errors.append(f"{model_name}: empty response")
                continue

            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{model_name}: invalid JSON ({exc})")
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")

    increment_metric("upstream_failures_total")
    attempted_models = ", ".join(models)
    last_error = errors[-1] if errors else "unknown error"
    raise HTTPException(
        status_code=502,
        detail=(
            f"Gemini upstream error after trying models [{attempted_models}]. "
            f"Last error: {last_error}. Set GOOGLE_API_KEY or update GEMINI_MODELS."
        ),
    )


def create_learning_router(
    get_genai_client: Callable[[], Any],
    increment_metric: Callable[[str, float], None],
    gemini_models: list[str],
) -> APIRouter:
    app_db.init()
    router = APIRouter(tags=["learning"])

    @router.post("/users/register", response_model=RegisterUserResponse, status_code=201)
    def register_user(req: RegisterUserRequest) -> RegisterUserResponse:
        user_id = req.user_id.strip()
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        created = app_db.create_user(user_id)
        if not created:
            raise HTTPException(status_code=409, detail="User ID already exists. Choose a unique user_id.")
        USER_PROFILES.pop(user_id, None)
        return RegisterUserResponse(user_id=user_id, created=True)

    @router.get("/users")
    def list_users():
        return {"users": app_db.list_users()}

    @router.post("/documents/upload", response_model=DocumentUploadResponse)
    async def upload_document(
        user_id: str = Form(...),
        file: UploadFile = File(...),
    ) -> DocumentUploadResponse:
        user_id = user_id.strip()
        _ensure_user_exists(user_id)

        filename = file.filename or "uploaded.txt"
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="File too large. Max 10MB")

        segments = _parse_document(filename, content)
        chunks, total_chars = _build_document_chunks(filename, segments)
        if not chunks:
            raise HTTPException(status_code=400, detail="Could not extract useful text from file")

        document_id = str(uuid.uuid4())
        document = {
            "document_id": document_id,
            "user_id": user_id,
            "filename": filename,
            "chunks": chunks,
            "char_count": total_chars,
            "created_at": int(time.time()),
        }
        DOCUMENTS[document_id] = document
        app_db.save_document(document)

        increment_metric("documents_uploaded_total")
        return DocumentUploadResponse(
            document_id=document_id,
            user_id=user_id,
            filename=filename,
            chunk_count=len(chunks),
            char_count=total_chars,
        )

    @router.get("/documents")
    def list_documents(user_id: str | None = Query(default=None)):
        if user_id:
            _ensure_user_exists(user_id)
        return {"documents": app_db.list_documents(user_id)}

    @router.post("/documents/ask", response_model=AskDocumentResponse)
    def ask_document(
        req: AskDocumentRequest,
    ) -> AskDocumentResponse:
        _ensure_user_exists(req.user_id)
        document = _get_document_or_404(req.document_id)
        selected_chunks = _rank_chunks(document["chunks"], req.question, req.top_k)

        context_lines = []
        for chunk in selected_chunks:
            context_lines.append(
                f"[{chunk['chunk_id']}] ({chunk['source']}) {chunk['text']}"
            )

        prompt = f"""
You are a tutoring assistant. Answer using ONLY the provided context.
If the answer is not in context, say that clearly.
Return ONLY valid JSON:
{{
  "answer": "...",
  "confidence": 0.0,
  "used_chunk_ids": ["c1", "c2"],
  "suggested_topics": ["topic1", "topic2"]
}}

Question:
{req.question}

Context:
{"\n".join(context_lines)}
""".strip()

        data = _call_gemini_json(
            prompt=prompt,
            models=gemini_models,
            get_genai_client=get_genai_client,
            increment_metric=increment_metric,
            max_output_tokens=700,
        )

        answer = str(data.get("answer", "")).strip()
        confidence = float(data.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))

        used_chunk_ids = set(_safe_string_list(data.get("used_chunk_ids"), max_items=req.top_k))
        if not used_chunk_ids:
            used_chunk_ids = {chunk["chunk_id"] for chunk in selected_chunks[:2]}

        chunk_map = {chunk["chunk_id"]: chunk for chunk in selected_chunks}
        sources: list[AskSource] = []
        for chunk_id in used_chunk_ids:
            chunk = chunk_map.get(chunk_id)
            if not chunk:
                continue
            sources.append(
                AskSource(
                    chunk_id=chunk["chunk_id"],
                    source=chunk["source"],
                    excerpt=chunk["text"][:220],
                )
            )

        suggested_topics = _safe_string_list(data.get("suggested_topics"), max_items=6)
        if not suggested_topics:
            suggested_topics = _extract_keywords(req.question, limit=4)

        profile = _get_or_create_profile(req.user_id)
        for topic in suggested_topics:
            profile["asked_topics"][topic.lower()] += 1

        increment_metric("qa_requests_total")

        request_id = str(uuid.uuid4())
        app_db.save_qa_log(
            request_id=request_id,
            document_id=req.document_id,
            user_id=req.user_id,
            question=req.question,
            answer=answer,
            confidence=confidence,
            suggested_topics=suggested_topics,
            sources=[source.model_dump() for source in sources],
        )

        USER_PROFILES[req.user_id] = app_db.get_user_profile_snapshot(req.user_id)

        return AskDocumentResponse(
            request_id=request_id,
            answer=answer,
            confidence=confidence,
            sources=sources,
            suggested_topics=suggested_topics,
        )

    @router.post("/quiz/generate", response_model=GenerateQuizResponse)
    def generate_quiz(
        req: GenerateQuizRequest,
    ) -> GenerateQuizResponse:
        _ensure_user_exists(req.user_id)
        document = _get_document_or_404(req.document_id)
        profile = _get_or_create_profile(req.user_id)

        focus_topics = _derive_focus_topics(profile, req.focus_topics)
        topic_query = " ".join(focus_topics) if focus_topics else "general overview"
        selected_chunks = _rank_chunks(document["chunks"], topic_query, top_k=10)

        context = "\n".join(
            f"[{chunk['chunk_id']}] {chunk['text']}" for chunk in selected_chunks
        )

        prompt = f"""
You are an assessment designer.
Create exactly {req.question_count} {req.difficulty} multiple-choice questions from context.
Return ONLY valid JSON:
{{
  "title": "...",
  "questions": [
    {{
      "question": "...",
      "options": ["A", "B", "C", "D"],
      "correct_answer": "...",
      "explanation": "...",
      "topic": "..."
    }}
  ]
}}

Focus topics: {", ".join(focus_topics) if focus_topics else "none"}
Context:
{context}
""".strip()

        data = _call_gemini_json(
            prompt=prompt,
            models=gemini_models,
            get_genai_client=get_genai_client,
            increment_metric=increment_metric,
            max_output_tokens=1400,
        )

        questions_raw = data.get("questions")
        if not isinstance(questions_raw, list) or not questions_raw:
            raise HTTPException(status_code=502, detail="Quiz generation failed")

        questions: list[QuizQuestion] = []
        for idx, item in enumerate(questions_raw[: req.question_count], start=1):
            if not isinstance(item, dict):
                continue
            options = _safe_string_list(item.get("options"), max_items=6)
            if len(options) < 2:
                continue
            correct_answer = str(item.get("correct_answer", options[0])).strip()
            if correct_answer not in options:
                correct_answer = options[0]

            questions.append(
                QuizQuestion(
                    id=f"q{idx}",
                    question=str(item.get("question", "")).strip() or f"Question {idx}",
                    options=options,
                    correct_answer=correct_answer,
                    explanation=str(item.get("explanation", "")).strip() or "",
                    topic=(str(item.get("topic", "general")).strip() or "general").lower(),
                )
            )

        if not questions:
            raise HTTPException(status_code=502, detail="Quiz generation returned invalid questions")

        increment_metric("quizzes_generated_total")

        quiz_id = str(uuid.uuid4())
        app_db.save_quiz(
            quiz_id=quiz_id,
            document_id=req.document_id,
            user_id=req.user_id,
            title=str(data.get("title", "Generated Quiz")).strip() or "Generated Quiz",
            difficulty=req.difficulty,
            focus_topics=focus_topics,
            questions=[q.model_dump() for q in questions],
        )

        return GenerateQuizResponse(
            quiz_id=quiz_id,
            title=str(data.get("title", "Generated Quiz")).strip() or "Generated Quiz",
            difficulty=req.difficulty,
            focus_topics=focus_topics,
            questions=questions,
        )

    @router.post("/quiz/submit", response_model=SubmitQuizResponse)
    def submit_quiz(
        req: SubmitQuizRequest,
    ) -> SubmitQuizResponse:
        _ensure_user_exists(req.user_id)
        _get_document_or_404(req.document_id)

        profile = _get_or_create_profile(req.user_id)
        answer_map = {answer.question_id: answer.selected_answer.strip() for answer in req.answers}

        score = 0
        results: list[QuizQuestionResult] = []

        for question in req.questions:
            selected = answer_map.get(question.id, "")
            correct = question.correct_answer.strip()
            is_correct = selected == correct
            if is_correct:
                score += 1
                if profile["weak_topics"][question.topic] > 0:
                    profile["weak_topics"][question.topic] -= 1
            else:
                profile["weak_topics"][question.topic] += 1

            results.append(
                QuizQuestionResult(
                    question_id=question.id,
                    topic=question.topic,
                    selected_answer=selected,
                    correct_answer=correct,
                    is_correct=is_correct,
                )
            )

        total = len(req.questions)
        accuracy = (score / total) if total else 0.0

        weak_topics = [topic for topic, count in profile["weak_topics"].most_common() if count > 0]
        recommended_topics = weak_topics[:4] if weak_topics else ["general_review"]

        increment_metric("quiz_submissions_total")

        submission_id = str(uuid.uuid4())
        app_db.save_quiz_submission(
            submission_id=submission_id,
            quiz_id=req.quiz_id,
            document_id=req.document_id,
            user_id=req.user_id,
            score=score,
            total=total,
            accuracy=accuracy,
            weak_topics=weak_topics[:6],
            recommended_topics=recommended_topics,
            results=[result.model_dump() for result in results],
        )
        USER_PROFILES[req.user_id] = app_db.get_user_profile_snapshot(req.user_id)

        return SubmitQuizResponse(
            score=score,
            total=total,
            accuracy=accuracy,
            weak_topics=weak_topics[:6],
            recommended_topics=recommended_topics,
            results=results,
        )

    @router.post("/flashcards/generate", response_model=GenerateFlashcardsResponse)
    def generate_flashcards(
        req: GenerateFlashcardsRequest,
    ) -> GenerateFlashcardsResponse:
        _ensure_user_exists(req.user_id)
        document = _get_document_or_404(req.document_id)
        profile = _get_or_create_profile(req.user_id)

        focus_topics = _derive_focus_topics(profile, req.focus_topics)
        topic_query = " ".join(focus_topics) if focus_topics else "key concepts"
        selected_chunks = _rank_chunks(document["chunks"], topic_query, top_k=10)

        prompt = f"""
You are a learning coach.
Create exactly {req.card_count} concise study flashcards from context.
Return ONLY valid JSON:
{{
  "title": "...",
  "cards": [
    {{"front": "...", "back": "...", "topic": "..."}}
  ]
}}

Focus topics: {", ".join(focus_topics) if focus_topics else "none"}
Context:
{"\n".join(chunk['text'] for chunk in selected_chunks)}
""".strip()

        data = _call_gemini_json(
            prompt=prompt,
            models=gemini_models,
            get_genai_client=get_genai_client,
            increment_metric=increment_metric,
            max_output_tokens=1600,
        )

        cards_raw = data.get("cards")
        if not isinstance(cards_raw, list) or not cards_raw:
            raise HTTPException(status_code=502, detail="Flashcard generation failed")

        cards: list[Flashcard] = []
        for idx, item in enumerate(cards_raw[: req.card_count], start=1):
            if not isinstance(item, dict):
                continue
            front = str(item.get("front", "")).strip()
            back = str(item.get("back", "")).strip()
            topic = (str(item.get("topic", "general")).strip() or "general").lower()
            if not front or not back:
                continue
            cards.append(
                Flashcard(
                    id=f"f{idx}",
                    front=front,
                    back=back,
                    topic=topic,
                )
            )

        if not cards:
            raise HTTPException(status_code=502, detail="Flashcard generation returned invalid cards")

        increment_metric("flashcards_generated_total")

        deck_id = str(uuid.uuid4())
        app_db.save_flashcard_deck(
            deck_id=deck_id,
            document_id=req.document_id,
            user_id=req.user_id,
            title=str(data.get("title", "Study Cards")).strip() or "Study Cards",
            cards=[card.model_dump() for card in cards],
        )

        return GenerateFlashcardsResponse(
            deck_id=deck_id,
            title=str(data.get("title", "Study Cards")).strip() or "Study Cards",
            cards=cards,
        )

    @router.post("/flashcards/review")
    def review_flashcard(
        req: ReviewFlashcardRequest,
    ):
        _ensure_user_exists(req.user_id)
        profile = _get_or_create_profile(req.user_id)
        topic = req.topic.strip().lower() or "general"

        if req.confidence <= 2:
            profile["weak_topics"][topic] += 1
        elif req.confidence >= 4 and profile["weak_topics"][topic] > 0:
            profile["weak_topics"][topic] -= 1

        app_db.log_flashcard_review(req.user_id, topic, req.confidence)
        USER_PROFILES[req.user_id] = app_db.get_user_profile_snapshot(req.user_id)

        increment_metric("flashcard_reviews_total")
        return {"status": "ok", "topic": topic, "confidence": req.confidence}

    @router.get("/users/{user_id}/progress", response_model=UserProgressResponse)
    def user_progress(
        user_id: str,
    ) -> UserProgressResponse:
        _ensure_user_exists(user_id)
        profile = app_db.get_user_profile_snapshot(user_id)
        USER_PROFILES[user_id] = profile

        answered = profile["answered_questions_total"]
        accuracy = (profile["correct_answers_total"] / answered) if answered else 0.0

        asked_topics = [
            TopicStat(topic=topic, count=count)
            for topic, count in profile["asked_topics"].most_common(8)
        ]
        weak_topics = [
            TopicStat(topic=topic, count=count)
            for topic, count in profile["weak_topics"].most_common(8)
            if count > 0
        ]

        recommendations: list[str] = []
        if weak_topics:
            recommendations.append(f"Review weak topics first: {', '.join(t.topic for t in weak_topics[:3])}")
        if asked_topics:
            recommendations.append(f"Generate focused quiz from asked topics: {', '.join(t.topic for t in asked_topics[:3])}")
        if not recommendations:
            recommendations.append("Upload a document and ask your first question to start adaptation.")

        return UserProgressResponse(
            user_id=user_id,
            quiz_attempts=profile["quiz_attempts"],
            answered_questions_total=answered,
            correct_answers_total=profile["correct_answers_total"],
            accuracy=accuracy,
            asked_topics=asked_topics,
            weak_topics=weak_topics,
            recommendations=recommendations,
        )

    return router
