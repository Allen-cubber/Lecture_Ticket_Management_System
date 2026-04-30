const form = document.querySelector("#student-query-form");
const input = document.querySelector("#student-id");
const resultPanel = document.querySelector("#student-result");
const errorBox = document.querySelector("#student-error");
const feedbackModal = document.querySelector("#feedback-modal");
const openFeedbackButton = document.querySelector("#open-feedback");
const feedbackForm = document.querySelector("#feedback-form");
const feedbackActivity = document.querySelector("#feedback-activity");
const feedbackTip = document.querySelector("#feedback-tip");

let currentStudentId = "";
let activityOptions = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { method: "GET", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "查询失败");
  }
  return payload;
}

function setText(selector, value) {
  document.querySelector(selector).textContent = String(value ?? "-");
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  resultPanel.classList.add("hidden");
}

function hideError() {
  errorBox.textContent = "";
  errorBox.classList.add("hidden");
}

function renderEvents(events) {
  const list = document.querySelector("#event-list");
  if (!events.length) {
    list.innerHTML = '<p class="student-empty">暂无讲座记录</p>';
    return;
  }

  list.innerHTML = events
    .map(
      (event) => `
        <div class="event-item">
          <div>
            <strong>${escapeHtml(event.activity_name)}</strong>
            <span>${escapeHtml(event.activity_time)}</span>
          </div>
          <time>${escapeHtml(event.imported_at)}</time>
        </div>
      `,
    )
    .join("");
}

function renderActivityOptions() {
  if (!activityOptions.length) {
    feedbackActivity.innerHTML = '<option value="">暂无可反馈讲座</option>';
    feedbackActivity.disabled = true;
    feedbackForm.querySelector('button[type="submit"]').disabled = true;
    feedbackTip.textContent = "后台还没有录入讲座活动，暂时不能提交反馈。";
    return;
  }

  feedbackActivity.disabled = false;
  feedbackForm.querySelector('button[type="submit"]').disabled = false;
  feedbackActivity.innerHTML = `
    <option value="">请选择讲座活动</option>
    ${activityOptions
      .map((activity) => `<option value="${escapeHtml(activity.activity_id)}">${escapeHtml(activity.label)}</option>`)
      .join("")}
  `;
}

function openFeedbackModal() {
  if (!currentStudentId) {
    showError("请先查询学号");
    return;
  }
  feedbackTip.textContent = "";
  feedbackModal.classList.remove("hidden");
  document.body.classList.add("modal-open");
  feedbackActivity.focus();
}

function closeFeedbackModal() {
  feedbackModal.classList.add("hidden");
  document.body.classList.remove("modal-open");
  openFeedbackButton.focus();
}

async function loadActivityOptions() {
  const data = await requestJson("/api/activities");
  activityOptions = data.activities || [];
  renderActivityOptions();
}

function renderResult(data) {
  const student = data.student;
  const status = document.querySelector("#result-status");
  currentStudentId = student.student_id;

  setText("#result-student-id", student.student_id);
  setText("#result-level", student.education_level);
  setText("#result-current", student.current_tickets);
  setText("#result-requirement", student.requirement);
  setText("#result-remaining", student.remaining);
  setText("#result-progress-text", student.progress_text);
  setText("#result-updated", `更新于 ${student.updated_at}`);

  status.textContent = student.complete ? "已达标" : "未达标";
  status.className = `status ${student.complete ? "ok" : "warn"}`;
  document.querySelector("#result-progress-bar").style.width = `${student.progress_percent}%`;
  renderEvents(data.events || []);

  hideError();
  resultPanel.classList.remove("hidden");
}

async function runQuery(studentId) {
  const id = studentId.trim();
  if (!id) {
    showError("请输入学号");
    return;
  }

  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = "查询中";
  try {
    const params = new URLSearchParams({ student_id: id });
    const data = await requestJson(`/api/student-query?${params.toString()}`);
    renderResult(data);
    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, "", nextUrl);
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "查询";
  }
}

async function submitFeedback() {
  if (!currentStudentId) {
    feedbackTip.textContent = "请先查询学号。";
    return;
  }

  const activityId = feedbackActivity.value;
  const message = document.querySelector("#feedback-message").value.trim();
  const contact = document.querySelector("#feedback-contact").value.trim();
  if (!activityId) {
    feedbackTip.textContent = "请选择讲座活动。";
    return;
  }
  if (!message) {
    feedbackTip.textContent = "请填写反馈意见。";
    return;
  }

  const button = feedbackForm.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "提交中";
  try {
    const data = await requestJson("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student_id: currentStudentId,
        activity_id: Number(activityId),
        message,
        contact,
      }),
    });
    feedbackTip.textContent = data.message || "反馈已提交";
    document.querySelector("#feedback-message").value = "";
    document.querySelector("#feedback-contact").value = "";
    window.setTimeout(closeFeedbackModal, 700);
  } catch (error) {
    feedbackTip.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "提交反馈";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  runQuery(input.value);
});

feedbackForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitFeedback();
});

openFeedbackButton.addEventListener("click", openFeedbackModal);

document.querySelectorAll("[data-close-feedback]").forEach((item) => {
  item.addEventListener("click", closeFeedbackModal);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !feedbackModal.classList.contains("hidden")) {
    closeFeedbackModal();
  }
});

const initialId = new URLSearchParams(window.location.search).get("student_id");
loadActivityOptions().catch((error) => {
  feedbackTip.textContent = error.message;
});
if (initialId) {
  input.value = initialId;
  runQuery(initialId);
}
