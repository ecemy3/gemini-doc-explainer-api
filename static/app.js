const state = {
  userId: "ecemm",
  lastDocumentId: "",
  currentQuiz: null,
};

const els = {
  userId: document.getElementById("userId"),
  documentId: document.getElementById("documentId"),
  credStatus: document.getElementById("credStatus"),
  uploadResult: document.getElementById("uploadResult"),
  askResult: document.getElementById("askResult"),
  quizResult: document.getElementById("quizResult"),
  flashResult: document.getElementById("flashResult"),
  progressResult: document.getElementById("progressResult"),
};

function setStatus(target, content) {
  target.innerHTML = content;
}

function topicsFromInput(value) {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body instanceof FormData ? options.body : options.body ? JSON.stringify(options.body) : undefined,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return data;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderQuiz(quiz) {
  const html = [`<div class="quiz-wrap">`, `<div><strong>${escapeHtml(quiz.title)}</strong></div>`];

  for (const q of quiz.questions) {
    html.push(`<div class="quiz-question" data-id="${q.id}">`);
    html.push(`<div><strong>${escapeHtml(q.question)}</strong></div>`);
    for (const opt of q.options) {
      html.push(
        `<label class="option"><input type="radio" name="${q.id}" value="${escapeHtml(opt)}" /> ${escapeHtml(opt)}</label>`
      );
    }
    html.push(`<div class="muted">Topic: ${escapeHtml(q.topic)}</div>`);
    html.push(`</div>`);
  }

  html.push(`<button id="submitQuizBtn" type="button">Quiz Sonucunu Hesapla</button>`);
  html.push(`</div>`);

  setStatus(els.quizResult, html.join(""));
  document.getElementById("submitQuizBtn").addEventListener("click", submitQuiz);
}

async function submitQuiz() {
  if (!state.currentQuiz) return;

  const answers = [];
  for (const q of state.currentQuiz.questions) {
    const selected = document.querySelector(`input[name="${q.id}"]:checked`);
    answers.push({ question_id: q.id, selected_answer: selected ? selected.value : "" });
  }

  try {
    const result = await api("/quiz/submit", {
      method: "POST",
      body: {
        document_id: state.lastDocumentId || els.documentId.value,
        user_id: state.userId,
        questions: state.currentQuiz.questions,
        answers,
      },
    });

    const details = result.results
      .map(
        (r) =>
          `<div class="stat-item">${escapeHtml(r.question_id)} | ${escapeHtml(r.topic)} | ${r.is_correct ? "dogru" : "yanlis"}</div>`
      )
      .join("");

    setStatus(
      els.quizResult,
      `<div><strong>Skor:</strong> ${result.score}/${result.total} (${(result.accuracy * 100).toFixed(1)}%)</div>
       <div><strong>Zayif Konular:</strong> ${result.weak_topics.join(", ") || "yok"}</div>
       <div><strong>Onerilen Konular:</strong> ${result.recommended_topics.join(", ") || "yok"}</div>
       ${details}`
    );
  } catch (error) {
    setStatus(els.quizResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
  }
}

function init() {
  document.getElementById("saveCreds").addEventListener("click", () => {
    const userId = (els.userId.value || "").trim();
    if (!userId) {
      setStatus(els.credStatus, "User ID gerekli.");
      return;
    }

    api("/users/register", {
      method: "POST",
      body: { user_id: userId },
    })
      .then(() => {
        state.userId = userId;
        setStatus(els.credStatus, "User olusturuldu. Artik bu ID tekildir.");
      })
      .catch((error) => {
        state.userId = userId;
        setStatus(els.credStatus, `Hata: ${escapeHtml(error.message)}`);
      });
  });

  document.getElementById("uploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("docFile");
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("user_id", state.userId || (els.userId.value || "").trim());
    formData.append("file", file);

    try {
      const result = await api("/documents/upload", { method: "POST", body: formData });
      state.lastDocumentId = result.document_id;
      els.documentId.value = result.document_id;
      setStatus(
        els.uploadResult,
        `<div><strong>Belge ID:</strong> ${escapeHtml(result.document_id)}</div>
         <div><strong>Parca:</strong> ${result.chunk_count}</div>
         <div><strong>Karakter:</strong> ${result.char_count}</div>`
      );
    } catch (error) {
      setStatus(els.uploadResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
    }
  });

  document.getElementById("askForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = document.getElementById("askQuestion").value.trim();
    const documentId = (els.documentId.value || state.lastDocumentId).trim();

    if (!documentId || !question) return;

    try {
      const result = await api("/documents/ask", {
        method: "POST",
        body: {
          document_id: documentId,
          user_id: state.userId,
          question,
          top_k: 4,
        },
      });

      const sources = result.sources
        .map(
          (s) =>
            `<div class="source-item"><strong>${escapeHtml(s.chunk_id)}</strong> - ${escapeHtml(s.source)}<br/>${escapeHtml(s.excerpt)}</div>`
        )
        .join("");

      setStatus(
        els.askResult,
        `<div><strong>Cevap:</strong> ${escapeHtml(result.answer)}</div>
         <div><strong>Confidence:</strong> ${(result.confidence * 100).toFixed(1)}%</div>
         <div><strong>Onerilen Konular:</strong> ${result.suggested_topics.join(", ") || "yok"}</div>
         ${sources}`
      );
    } catch (error) {
      setStatus(els.askResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
    }
  });

  document.getElementById("quizForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const documentId = (els.documentId.value || state.lastDocumentId).trim();
    if (!documentId) return;

    try {
      const result = await api("/quiz/generate", {
        method: "POST",
        body: {
          document_id: documentId,
          user_id: state.userId,
          question_count: Number(document.getElementById("quizCount").value || 5),
          difficulty: document.getElementById("quizDifficulty").value,
          focus_topics: topicsFromInput(document.getElementById("quizTopics").value),
        },
      });

      state.currentQuiz = result;
      renderQuiz(result);
    } catch (error) {
      setStatus(els.quizResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
    }
  });

  document.getElementById("flashForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const documentId = (els.documentId.value || state.lastDocumentId).trim();
    if (!documentId) return;

    try {
      const result = await api("/flashcards/generate", {
        method: "POST",
        body: {
          document_id: documentId,
          user_id: state.userId,
          card_count: Number(document.getElementById("cardCount").value || 10),
          focus_topics: topicsFromInput(document.getElementById("flashTopics").value),
        },
      });

      const cards = result.cards
        .map(
          (card) => `<div class="card-item">
            <strong>${escapeHtml(card.front)}</strong>
            <div>${escapeHtml(card.back)}</div>
            <div class="muted">${escapeHtml(card.topic)}</div>
            <div class="row">
              <button type="button" class="review-btn" data-topic="${escapeHtml(card.topic)}" data-confidence="2">Zorlandim</button>
              <button type="button" class="review-btn" data-topic="${escapeHtml(card.topic)}" data-confidence="5">Bildim</button>
            </div>
          </div>`
        )
        .join("");

      setStatus(els.flashResult, `<div><strong>${escapeHtml(result.title)}</strong></div>${cards}`);

      for (const button of document.querySelectorAll(".review-btn")) {
        button.addEventListener("click", async () => {
          try {
            await api("/flashcards/review", {
              method: "POST",
              body: {
                user_id: state.userId,
                topic: button.dataset.topic,
                confidence: Number(button.dataset.confidence),
              },
            });
            button.textContent = "Kaydedildi";
            button.disabled = true;
          } catch (error) {
            button.textContent = "Hata";
          }
        });
      }
    } catch (error) {
      setStatus(els.flashResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
    }
  });

  document.getElementById("loadProgress").addEventListener("click", async () => {
    try {
      const result = await api(`/users/${encodeURIComponent(state.userId)}/progress`);
      const asked = result.asked_topics
        .map((t) => `<div class="stat-item">${escapeHtml(t.topic)}: ${t.count}</div>`)
        .join("");
      const weak = result.weak_topics
        .map((t) => `<div class="stat-item">${escapeHtml(t.topic)}: ${t.count}</div>`)
        .join("");
      const recommendations = result.recommendations
        .map((r) => `<div class="stat-item">${escapeHtml(r)}</div>`)
        .join("");

      setStatus(
        els.progressResult,
        `<div><strong>Quiz Attempt:</strong> ${result.quiz_attempts}</div>
         <div><strong>Toplam Soru:</strong> ${result.answered_questions_total}</div>
         <div><strong>Dogru:</strong> ${result.correct_answers_total}</div>
         <div><strong>Basari:</strong> ${(result.accuracy * 100).toFixed(1)}%</div>
         <div><strong>Sorulan Konular</strong></div>${asked || "<div class='muted'>yok</div>"}
         <div><strong>Zayif Konular</strong></div>${weak || "<div class='muted'>yok</div>"}
         <div><strong>Oneriler</strong></div>${recommendations}`
      );
    } catch (error) {
      setStatus(els.progressResult, `<div class="muted">Hata: ${escapeHtml(error.message)}</div>`);
    }
  });
}

init();
