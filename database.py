import json
import os
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any


class AppDatabase:
    ADMIN_TABLES = (
        "users",
        "documents",
        "document_chunks",
        "qa_logs",
        "quizzes",
        "quiz_questions",
        "quiz_submissions",
        "quiz_submission_results",
        "flashcard_decks",
        "flashcards",
        "flashcard_reviews",
    )

    def __init__(self, db_path: str | None = None):
        configured_path = db_path or os.getenv("DATABASE_PATH", "ai_doc_explainer.db")
        self.db_path = Path(configured_path)
        self._lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self._lock:
            if self._initialized:
                return

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        created_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS documents (
                        document_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        char_count INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );

                    CREATE TABLE IF NOT EXISTS document_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        document_id TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        text TEXT NOT NULL,
                        terms_json TEXT NOT NULL,
                        FOREIGN KEY(document_id) REFERENCES documents(document_id)
                    );

                    CREATE TABLE IF NOT EXISTS qa_logs (
                        request_id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        suggested_topics_json TEXT NOT NULL,
                        sources_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(document_id) REFERENCES documents(document_id),
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );

                    CREATE TABLE IF NOT EXISTS quizzes (
                        quiz_id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        difficulty TEXT NOT NULL,
                        focus_topics_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(document_id) REFERENCES documents(document_id),
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );

                    CREATE TABLE IF NOT EXISTS quiz_questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        quiz_id TEXT NOT NULL,
                        question_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        options_json TEXT NOT NULL,
                        correct_answer TEXT NOT NULL,
                        explanation TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        FOREIGN KEY(quiz_id) REFERENCES quizzes(quiz_id)
                    );

                    CREATE TABLE IF NOT EXISTS quiz_submissions (
                        submission_id TEXT PRIMARY KEY,
                        quiz_id TEXT,
                        document_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        score INTEGER NOT NULL,
                        total INTEGER NOT NULL,
                        accuracy REAL NOT NULL,
                        weak_topics_json TEXT NOT NULL,
                        recommended_topics_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(document_id) REFERENCES documents(document_id),
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );

                    CREATE TABLE IF NOT EXISTS quiz_submission_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        submission_id TEXT NOT NULL,
                        question_id TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        selected_answer TEXT NOT NULL,
                        correct_answer TEXT NOT NULL,
                        is_correct INTEGER NOT NULL,
                        FOREIGN KEY(submission_id) REFERENCES quiz_submissions(submission_id)
                    );

                    CREATE TABLE IF NOT EXISTS flashcard_decks (
                        deck_id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(document_id) REFERENCES documents(document_id),
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );

                    CREATE TABLE IF NOT EXISTS flashcards (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        deck_id TEXT NOT NULL,
                        card_id TEXT NOT NULL,
                        front TEXT NOT NULL,
                        back TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        FOREIGN KEY(deck_id) REFERENCES flashcard_decks(deck_id)
                    );

                    CREATE TABLE IF NOT EXISTS flashcard_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        confidence INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );
                    """
                )

            self._initialized = True

    def clear_all(self) -> None:
        self.init()
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM flashcard_reviews;
                DELETE FROM flashcards;
                DELETE FROM flashcard_decks;
                DELETE FROM quiz_submission_results;
                DELETE FROM quiz_submissions;
                DELETE FROM quiz_questions;
                DELETE FROM quizzes;
                DELETE FROM qa_logs;
                DELETE FROM document_chunks;
                DELETE FROM documents;
                DELETE FROM users;
                """
            )

    def create_user(self, user_id: str) -> bool:
        self.init()
        ts = int(time.time())
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                    (user_id, ts),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def user_exists(self, user_id: str) -> bool:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def list_users(self) -> list[str]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id FROM users ORDER BY created_at ASC").fetchall()
        return [str(row["user_id"]) for row in rows]

    def get_table_counts(self) -> dict[str, int]:
        self.init()
        result: dict[str, int] = {}
        with self._connect() as conn:
            for table in self.ADMIN_TABLES:
                row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
                result[table] = int(row["cnt"] if row else 0)
        return result

    def get_table_rows(self, table_name: str, limit: int = 20) -> list[dict[str, Any]]:
        self.init()
        table = table_name.strip().lower()
        if table not in self.ADMIN_TABLES:
            raise ValueError(f"Unsupported table: {table_name}")

        safe_limit = max(1, min(int(limit), 200))

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            for key, value in list(record.items()):
                if key.endswith("_json") and isinstance(value, str):
                    try:
                        record[key] = json.loads(value)
                    except Exception:
                        pass
            records.append(record)
        return records

    def save_document(self, document: dict[str, Any]) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents (document_id, user_id, filename, char_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document["document_id"],
                    document["user_id"],
                    document["filename"],
                    document["char_count"],
                    document["created_at"],
                ),
            )
            conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document["document_id"],))
            conn.executemany(
                """
                INSERT INTO document_chunks (document_id, chunk_id, source, text, terms_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        document["document_id"],
                        chunk["chunk_id"],
                        chunk["source"],
                        chunk["text"],
                        json.dumps(sorted(list(chunk["terms"]))),
                    )
                    for chunk in document["chunks"]
                ],
            )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        self.init()
        with self._connect() as conn:
            doc = conn.execute(
                "SELECT document_id, user_id, filename, char_count, created_at FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if not doc:
                return None

            chunk_rows = conn.execute(
                """
                SELECT chunk_id, source, text, terms_json
                FROM document_chunks
                WHERE document_id = ?
                ORDER BY id ASC
                """,
                (document_id,),
            ).fetchall()

        chunks = []
        for row in chunk_rows:
            chunks.append(
                {
                    "chunk_id": row["chunk_id"],
                    "source": row["source"],
                    "text": row["text"],
                    "terms": set(json.loads(row["terms_json"])),
                }
            )

        return {
            "document_id": doc["document_id"],
            "user_id": doc["user_id"],
            "filename": doc["filename"],
            "char_count": doc["char_count"],
            "created_at": doc["created_at"],
            "chunks": chunks,
        }

    def list_documents(self, user_id: str | None = None) -> list[dict[str, Any]]:
        self.init()
        query = """
            SELECT d.document_id, d.user_id, d.filename, d.char_count, d.created_at,
                   COUNT(c.id) AS chunk_count
            FROM documents d
            LEFT JOIN document_chunks c ON c.document_id = d.document_id
        """
        params: tuple[Any, ...] = ()
        if user_id:
            query += " WHERE d.user_id = ?"
            params = (user_id,)
        query += " GROUP BY d.document_id ORDER BY d.created_at DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "document_id": row["document_id"],
                "user_id": row["user_id"],
                "filename": row["filename"],
                "char_count": row["char_count"],
                "created_at": row["created_at"],
                "chunk_count": int(row["chunk_count"]),
            }
            for row in rows
        ]

    def save_qa_log(
        self,
        request_id: str,
        document_id: str,
        user_id: str,
        question: str,
        answer: str,
        confidence: float,
        suggested_topics: list[str],
        sources: list[dict[str, Any]],
    ) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO qa_logs (
                    request_id, document_id, user_id, question, answer,
                    confidence, suggested_topics_json, sources_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    document_id,
                    user_id,
                    question,
                    answer,
                    confidence,
                    json.dumps(suggested_topics),
                    json.dumps(sources),
                    int(time.time()),
                ),
            )

    def save_quiz(
        self,
        quiz_id: str,
        document_id: str,
        user_id: str,
        title: str,
        difficulty: str,
        focus_topics: list[str],
        questions: list[dict[str, Any]],
    ) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quizzes (quiz_id, document_id, user_id, title, difficulty, focus_topics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quiz_id,
                    document_id,
                    user_id,
                    title,
                    difficulty,
                    json.dumps(focus_topics),
                    int(time.time()),
                ),
            )
            conn.execute("DELETE FROM quiz_questions WHERE quiz_id = ?", (quiz_id,))
            conn.executemany(
                """
                INSERT INTO quiz_questions (
                    quiz_id, question_id, question, options_json, correct_answer, explanation, topic
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        quiz_id,
                        q["id"],
                        q["question"],
                        json.dumps(q["options"]),
                        q["correct_answer"],
                        q["explanation"],
                        q["topic"],
                    )
                    for q in questions
                ],
            )

    def save_quiz_submission(
        self,
        submission_id: str,
        quiz_id: str | None,
        document_id: str,
        user_id: str,
        score: int,
        total: int,
        accuracy: float,
        weak_topics: list[str],
        recommended_topics: list[str],
        results: list[dict[str, Any]],
    ) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quiz_submissions (
                    submission_id, quiz_id, document_id, user_id, score, total,
                    accuracy, weak_topics_json, recommended_topics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    quiz_id,
                    document_id,
                    user_id,
                    score,
                    total,
                    accuracy,
                    json.dumps(weak_topics),
                    json.dumps(recommended_topics),
                    int(time.time()),
                ),
            )
            conn.executemany(
                """
                INSERT INTO quiz_submission_results (
                    submission_id, question_id, topic, selected_answer, correct_answer, is_correct
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        submission_id,
                        r["question_id"],
                        r["topic"],
                        r["selected_answer"],
                        r["correct_answer"],
                        1 if r["is_correct"] else 0,
                    )
                    for r in results
                ],
            )

    def save_flashcard_deck(
        self,
        deck_id: str,
        document_id: str,
        user_id: str,
        title: str,
        cards: list[dict[str, Any]],
    ) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO flashcard_decks (deck_id, document_id, user_id, title, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (deck_id, document_id, user_id, title, int(time.time())),
            )
            conn.execute("DELETE FROM flashcards WHERE deck_id = ?", (deck_id,))
            conn.executemany(
                """
                INSERT INTO flashcards (deck_id, card_id, front, back, topic)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (deck_id, card["id"], card["front"], card["back"], card["topic"])
                    for card in cards
                ],
            )

    def log_flashcard_review(self, user_id: str, topic: str, confidence: int) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO flashcard_reviews (user_id, topic, confidence, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, topic, confidence, int(time.time())),
            )

    def get_user_profile_snapshot(self, user_id: str) -> dict[str, Any]:
        self.init()
        asked_topics = Counter()
        weak_topics = Counter()

        with self._connect() as conn:
            stats_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS quiz_attempts,
                    COALESCE(SUM(total), 0) AS answered_total,
                    COALESCE(SUM(score), 0) AS correct_total
                FROM quiz_submissions
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            qa_rows = conn.execute(
                "SELECT suggested_topics_json FROM qa_logs WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            for row in qa_rows:
                for topic in json.loads(row["suggested_topics_json"]):
                    if isinstance(topic, str) and topic.strip():
                        asked_topics[topic.strip().lower()] += 1

            wrong_rows = conn.execute(
                """
                SELECT topic, COUNT(*) AS cnt
                FROM quiz_submission_results qsr
                JOIN quiz_submissions qs ON qs.submission_id = qsr.submission_id
                WHERE qs.user_id = ? AND qsr.is_correct = 0
                GROUP BY topic
                """,
                (user_id,),
            ).fetchall()
            for row in wrong_rows:
                weak_topics[row["topic"].strip().lower()] += int(row["cnt"])

            review_rows = conn.execute(
                "SELECT topic, confidence FROM flashcard_reviews WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            for row in review_rows:
                topic = row["topic"].strip().lower()
                confidence = int(row["confidence"])
                if confidence <= 2:
                    weak_topics[topic] += 1
                elif confidence >= 4 and weak_topics[topic] > 0:
                    weak_topics[topic] -= 1

        return {
            "asked_topics": asked_topics,
            "weak_topics": weak_topics,
            "quiz_attempts": int(stats_row["quiz_attempts"] if stats_row else 0),
            "answered_questions_total": int(stats_row["answered_total"] if stats_row else 0),
            "correct_answers_total": int(stats_row["correct_total"] if stats_row else 0),
        }


app_db = AppDatabase()
