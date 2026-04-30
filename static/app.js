const state = {
  status: "all",
  search: "",
  rules: {},
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.href = `/admin/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("请先登录后台");
  }
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function setMetric(id, value) {
  $(id).textContent = String(value ?? 0);
}

function renderSummary(summary) {
  setMetric("#metric-students", summary.students);
  setMetric("#metric-tickets", summary.tickets);
  setMetric("#metric-completed", summary.completed);
  setMetric("#metric-unfinished", summary.unfinished);
}

function renderRules(rules) {
  const list = $("#rules-list");
  const entries = Object.entries(rules || {});
  list.innerHTML = entries
    .map(
      ([level, requirement]) => `
        <div class="rule-item">
          <span>${escapeHtml(level)}</span>
          <strong>${escapeHtml(requirement)} 张</strong>
        </div>
      `,
    )
    .join("");
}

function ruleOptions(selected) {
  return Object.keys(state.rules)
    .map((level) => {
      const isSelected = level === selected ? "selected" : "";
      return `<option value="${escapeHtml(level)}" ${isSelected}>${escapeHtml(level)}</option>`;
    })
    .join("");
}

function renderStudents(students) {
  const body = $("#students-body");
  if (!students.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">暂无数据</td></tr>';
    return;
  }

  body.innerHTML = students
    .map((student) => {
      const requirement = Number(student.requirement || 0);
      const current = Number(student.current_tickets || 0);
      const complete = Boolean(student.complete);
      const percent = requirement === 0 ? 100 : Math.min(100, Math.round((current / requirement) * 100));
      const statusClass = complete ? "ok" : "warn";
      const statusText = complete ? "达标" : "未达标";
      return `
        <tr data-id="${escapeHtml(student.student_id)}">
          <td>${escapeHtml(student.student_id)}</td>
          <td>${escapeHtml(student.name)}</td>
          <td>
            <select class="level-select" aria-label="学历层次">
              ${ruleOptions(student.education_level)}
            </select>
          </td>
          <td><input class="ticket-input" type="number" min="0" step="1" value="${escapeHtml(current)}" aria-label="讲座票数量"></td>
          <td>${escapeHtml(requirement)}</td>
          <td>
            <div class="progress">
              <span>${escapeHtml(student.progress_text)}</span>
              <div class="progress-track"><div class="progress-bar" style="width: ${percent}%"></div></div>
            </div>
          </td>
          <td>
            <span class="status ${statusClass}">${statusText}</span>
            <button class="save-row" type="button">保存</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderFeedbackOld(items) {
  const body = $("#feedback-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">暂无反馈</td></tr>';
    return;
  }

  body.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.created_at)}</td>
          <td>${escapeHtml(item.student_id)}</td>
          <td>${escapeHtml(item.student_name)}</td>
          <td>${escapeHtml(item.activity_name)}</td>
          <td>${escapeHtml(item.activity_time)}</td>
          <td>${escapeHtml(item.message)}</td>
          <td>${escapeHtml(item.contact || "-")}</td>
        </tr>
      `,
    )
    .join("");
}

function renderFeedback(items) {
  const body = $("#feedback-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">暂无反馈</td></tr>';
    return;
  }

  body.innerHTML = items
    .map((item) => {
      const canGrant = !item.ticket_exists && !item.ticket_granted;
      const ticketText = item.ticket_granted ? "已补票" : item.ticket_exists ? "已计入" : "确认补票";
      return `
        <tr data-feedback-id="${escapeHtml(item.id)}">
          <td>${escapeHtml(item.created_at)}</td>
          <td>${escapeHtml(item.student_id)}</td>
          <td>${escapeHtml(item.student_name)}</td>
          <td>${escapeHtml(item.activity_name)}</td>
          <td>${escapeHtml(item.activity_time)}</td>
          <td>
            <select class="feedback-status-select" aria-label="反馈状态">
              <option value="pending" ${item.status === "pending" ? "selected" : ""}>未处理</option>
              <option value="resolved" ${item.status === "resolved" ? "selected" : ""}>已处理</option>
              <option value="rejected" ${item.status === "rejected" ? "selected" : ""}>已驳回</option>
            </select>
            ${item.ticket_granted ? '<span class="status ok">已补票</span>' : ""}
          </td>
          <td>${escapeHtml(item.message)}</td>
          <td>${escapeHtml(item.contact || "-")}</td>
          <td>
            <textarea class="feedback-note" rows="2" maxlength="500" aria-label="管理员备注">${escapeHtml(item.admin_note || "")}</textarea>
            ${item.handled_at ? `<span class="feedback-handled">处理于 ${escapeHtml(item.handled_at)}</span>` : ""}
          </td>
          <td>
            <div class="feedback-actions">
              <button class="save-feedback" type="button">保存状态</button>
              <button class="grant-ticket" type="button" ${canGrant ? "" : "disabled"}>${ticketText}</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function loadStudents() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.status !== "all") params.set("status", state.status);

  const data = await requestJson(`/api/students?${params.toString()}`);
  state.rules = data.rules || {};
  renderSummary(data.summary || {});
  renderRules(state.rules);
  renderStudents(data.students || []);
}

async function loadFeedback() {
  const data = await requestJson("/api/feedback");
  renderFeedback(data.feedback || []);
}

function fileNameBinder(inputSelector, labelSelector) {
  const input = $(inputSelector);
  const label = $(labelSelector);
  input.addEventListener("change", () => {
    label.textContent = input.files?.[0]?.name || "选择 Excel";
  });
}

function showToast(message) {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
}

function showResult(data, title) {
  const panel = $("#result-panel");
  const failures = data.failures || [];
  const failureRows = failures
    .slice(0, 100)
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.row || "")}</td>
          <td>${escapeHtml(item.student_id || "")}</td>
          <td>${escapeHtml(item.name || "")}</td>
          <td>${escapeHtml(item.reason || "")}</td>
        </tr>
      `,
    )
    .join("");

  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="result-title">
      <strong>${escapeHtml(title)}：${escapeHtml(data.message || "处理完成")}</strong>
      <button class="close-result" type="button" aria-label="关闭">×</button>
    </div>
    ${data.backup ? `<p>已自动备份：${escapeHtml(data.backup)}</p>` : ""}
    ${
      failures.length
        ? `<table class="failure-table">
            <thead><tr><th>行号</th><th>学号</th><th>姓名</th><th>原因</th></tr></thead>
            <tbody>${failureRows}</tbody>
          </table>
          ${failures.length > 100 ? `<p>仅显示前 100 条失败记录，共 ${failures.length} 条。</p>` : ""}`
        : "<p>没有失败记录。</p>"
    }
  `;
}

function bindUpload(formSelector, url, title) {
  const form = $(formSelector);
  const input = form.querySelector('input[type="file"]');
  const submit = form.querySelector('button[type="submit"]');

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!input.files?.length) {
      showToast("请先选择 Excel 文件");
      return;
    }

    const formData = new FormData();
    formData.append("file", input.files[0]);
    submit.disabled = true;
    submit.textContent = "导入中";
    try {
      const data = await requestJson(url, { method: "POST", body: formData });
      showResult(data, title);
      input.value = "";
      form.querySelector(".file-picker span").textContent = "选择 Excel";
      await loadStudents();
    } catch (error) {
      showToast(error.message);
    } finally {
      submit.disabled = false;
      submit.textContent = title === "学生名单导入" ? "导入学生名单" : "导入讲座记录";
    }
  });
}

function bindFilters() {
  let timer = 0;
  $("#search-input").addEventListener("input", (event) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(async () => {
      state.search = event.target.value.trim();
      await loadStudents().catch((error) => showToast(error.message));
    }, 240);
  });

  document.querySelectorAll(".segmented button").forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll(".segmented button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.status = button.dataset.status || "all";
      await loadStudents().catch((error) => showToast(error.message));
    });
  });
}

function bindTableActions() {
  $("#students-body").addEventListener("click", async (event) => {
    const button = event.target.closest(".save-row");
    if (!button) return;

    const row = button.closest("tr");
    const studentId = row.dataset.id;
    const currentTickets = row.querySelector(".ticket-input").value;
    const educationLevel = row.querySelector(".level-select").value;

    button.disabled = true;
    button.textContent = "保存中";
    try {
      await requestJson("/api/students/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_id: studentId,
          current_tickets: Number(currentTickets),
          education_level: educationLevel,
        }),
      });
      showToast("已保存");
      await loadStudents();
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "保存";
    }
  });
}

function bindResultPanel() {
  $("#result-panel").addEventListener("click", (event) => {
    if (event.target.closest(".close-result")) {
      $("#result-panel").classList.add("hidden");
    }
  });
}

function bindFeedbackActions() {
  $("#refresh-feedback").addEventListener("click", async () => {
    try {
      await loadFeedback();
      showToast("反馈记录已刷新");
    } catch (error) {
      showToast(error.message);
    }
  });

  $("#feedback-body").addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-feedback-id]");
    if (!row) return;

    const feedbackId = Number(row.dataset.feedbackId);
    const status = row.querySelector(".feedback-status-select").value;
    const adminNote = row.querySelector(".feedback-note").value.trim();

    if (event.target.closest(".save-feedback")) {
      try {
        const data = await requestJson("/api/feedback/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: feedbackId, status, admin_note: adminNote }),
        });
        showToast(data.backup ? "反馈状态已保存，已自动备份" : "反馈状态已保存");
        await loadFeedback();
      } catch (error) {
        showToast(error.message);
      }
    }

    if (event.target.closest(".grant-ticket")) {
      const confirmed = window.confirm("确认给该学生补加这次讲座票吗？系统会自动备份数据库。");
      if (!confirmed) return;
      try {
        const data = await requestJson("/api/feedback/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: feedbackId,
            status: "resolved",
            admin_note: adminNote,
            grant_ticket: true,
          }),
        });
        showToast(data.backup ? "已补票并更新反馈状态，已自动备份" : "已补票并更新反馈状态");
        await loadStudents();
        await loadFeedback();
      } catch (error) {
        showToast(error.message);
      }
    }
  });
}

function bindLogout() {
  $("#logout-button").addEventListener("click", async () => {
    await fetch("/api/admin/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/admin/login";
  });
}

async function init() {
  fileNameBinder("#student-file", "#student-file-name");
  fileNameBinder("#event-file", "#event-file-name");
  bindUpload("#student-import-form", "/api/import/students", "学生名单导入");
  bindUpload("#event-import-form", "/api/import/events", "讲座活动导入");
  bindFilters();
  bindTableActions();
  bindResultPanel();
  bindFeedbackActions();
  bindLogout();
  await loadStudents();
  await loadFeedback();
}

init().catch((error) => showToast(error.message));
