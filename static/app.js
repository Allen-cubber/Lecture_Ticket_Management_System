const state = {
  status: "all",
  search: "",
  rules: {},
  students: [],
  studentPage: 1,
  studentsPerPage: 15,
  feedbackItems: [],
  feedbackStatus: "pending",
  selectedFeedbackIds: new Set(),
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

function updateStudentPagination(totalItems) {
  const totalPages = Math.max(1, Math.ceil(totalItems / state.studentsPerPage));
  if (state.studentPage > totalPages) {
    state.studentPage = totalPages;
  }

  const prevButton = $("#students-prev");
  const nextButton = $("#students-next");
  const pageInfo = $("#students-page-info");

  prevButton.disabled = state.studentPage <= 1;
  nextButton.disabled = state.studentPage >= totalPages;
  pageInfo.textContent = `第 ${state.studentPage} 页 / 共 ${totalPages} 页`;
}

function renderStudents(students) {
  const body = $("#students-body");
  if (!students.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">暂无数据</td></tr>';
    updateStudentPagination(0);
    return;
  }

  const startIndex = (state.studentPage - 1) * state.studentsPerPage;
  const pageStudents = students.slice(startIndex, startIndex + state.studentsPerPage);

  body.innerHTML = pageStudents
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
          <td>${escapeHtml(student.education_level)}</td>
          <td><strong class="ticket-count">${escapeHtml(current)}</strong></td>
          <td>${escapeHtml(requirement)}</td>
          <td>
            <div class="progress">
              <span>${escapeHtml(student.progress_text)}</span>
              <div class="progress-track"><div class="progress-bar" style="width: ${percent}%"></div></div>
            </div>
            <span class="status ${statusClass}">${statusText}</span>
          </td>
        </tr>
      `;
    })
    .join("");

  updateStudentPagination(students.length);
}

function feedbackStatusLabel(status) {
  if (status === "resolved") return "已处理";
  if (status === "rejected") return "已驳回";
  return "未处理";
}

function filterFeedback(items) {
  if (state.feedbackStatus === "resolved") {
    return items.filter((item) => item.status !== "pending");
  }
  return items.filter((item) => item.status === "pending");
}

function updateFeedbackBatchState() {
  const selectedCount = state.selectedFeedbackIds.size;
  const countLabel = $("#feedback-selected-count");
  const grantButton = $("#feedback-batch-grant");
  const rejectButton = $("#feedback-batch-reject");
  const deleteButton = $("#feedback-batch-delete");
  const selectAll = $("#feedback-select-all");
  const visibleIds = filterFeedback(state.feedbackItems).map((item) => String(item.id));
  const visibleSelected = visibleIds.filter((id) => state.selectedFeedbackIds.has(id));

  if (countLabel) countLabel.textContent = `已选 ${selectedCount} 项`;
  if (grantButton) grantButton.disabled = selectedCount === 0;
  if (rejectButton) rejectButton.disabled = selectedCount === 0;
  if (deleteButton) deleteButton.disabled = selectedCount === 0 || state.feedbackStatus !== "resolved";
  if (selectAll) {
    selectAll.checked = visibleIds.length > 0 && visibleSelected.length === visibleIds.length;
    selectAll.indeterminate = visibleSelected.length > 0 && visibleSelected.length < visibleIds.length;
  }
}

function renderFeedback(items) {
  const body = $("#feedback-body");
  const filteredItems = filterFeedback(items);

  if (!filteredItems.length) {
    body.innerHTML = `<div class="empty">${state.feedbackStatus === "resolved" ? "暂无已处理反馈" : "暂无未处理反馈"}</div>`;
    state.selectedFeedbackIds.clear();
    updateFeedbackBatchState();
    return;
  }

  body.innerHTML = `
    <div class="feedback-list-head">
      <input id="feedback-select-all" type="checkbox" aria-label="选择当前反馈">
      <span>提交时间</span>
      <span>学生</span>
      <span>讲座活动</span>
      <span>补票</span>
      <span>驳回</span>
      <span>删除</span>
      <span>详情</span>
    </div>
  ` + filteredItems
    .map((item) => {
      const itemId = String(item.id);
      const cannotGrant = item.ticket_granted || (item.ticket_exists && !item.ticket_granted);
      const grantText = item.ticket_granted ? "已补票" : item.ticket_exists ? "已计入" : "补票";
      const rejectSelected = item.status === "rejected";
      const rejectText = rejectSelected ? "已驳回" : "驳回";
      const canDelete = item.status !== "pending";
      const checked = state.selectedFeedbackIds.has(itemId) ? "checked" : "";
      const handledText = item.handled_at ? `处理时间：${escapeHtml(item.handled_at)}` : "尚未处理";

      return `
        <details class="feedback-row" data-feedback-id="${escapeHtml(item.id)}">
          <summary class="feedback-summary">
            <input class="feedback-select" type="checkbox" value="${escapeHtml(item.id)}" aria-label="选择反馈" ${checked}>
            <span class="feedback-col feedback-col-time">${escapeHtml(item.created_at)}</span>
            <span class="feedback-col feedback-col-student">
              <strong>${escapeHtml(item.student_name)}</strong>
              <span>${escapeHtml(item.student_id)}</span>
            </span>
            <span class="feedback-col feedback-col-activity">${escapeHtml(item.activity_name)}</span>
            <button class="feedback-action-button grant-ticket ${item.ticket_granted ? "selected" : ""}" type="button" ${cannotGrant ? "disabled" : ""}>${grantText}</button>
            <button class="feedback-action-button reject-feedback ${rejectSelected ? "selected" : ""}" type="button" ${rejectSelected ? "disabled" : ""}>${rejectText}</button>
            <button class="feedback-action-button delete-feedback" type="button" ${canDelete ? "" : "disabled"}>删除</button>
            <span class="feedback-expand-text">详情</span>
          </summary>

          <div class="feedback-detail-grid">
            <div class="feedback-detail-item">
              <span class="feedback-label">活动时间</span>
              <p>${escapeHtml(item.activity_time)}</p>
            </div>
            <div class="feedback-detail-item">
              <span class="feedback-label">联系方式</span>
              <p>${escapeHtml(item.contact || "-")}</p>
            </div>
            <div class="feedback-detail-item">
              <span class="feedback-label">处理结果</span>
              <p>${feedbackStatusLabel(item.status)}，${handledText}${item.ticket_granted ? "，已通过补票处理" : ""}</p>
            </div>
            <div class="feedback-detail-item feedback-detail-wide">
              <span class="feedback-label">反馈内容</span>
              <p class="feedback-message">${escapeHtml(item.message)}</p>
            </div>
            <div class="feedback-detail-item feedback-detail-wide">
              <label class="feedback-label" for="feedback-note-${escapeHtml(item.id)}">管理员备注</label>
              <textarea id="feedback-note-${escapeHtml(item.id)}" class="feedback-note" rows="3" maxlength="500" aria-label="管理员备注">${escapeHtml(item.admin_note || "")}</textarea>
              <span class="feedback-subtext">修改备注后移出输入框会自动保存</span>
            </div>
          </div>
        </details>
      `;
    })
    .join("");
  updateFeedbackBatchState();
}

async function loadStudents() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.status !== "all") params.set("status", state.status);

  const data = await requestJson(`/api/students?${params.toString()}`);
  state.rules = data.rules || {};
  state.students = data.students || [];
  renderSummary(data.summary || {});
  renderRules(state.rules);
  renderStudents(state.students);
}

async function loadFeedback() {
  const data = await requestJson("/api/feedback");
  state.feedbackItems = data.feedback || [];
  const existingIds = new Set(state.feedbackItems.map((item) => String(item.id)));
  Array.from(state.selectedFeedbackIds).forEach((id) => {
    if (!existingIds.has(id)) state.selectedFeedbackIds.delete(id);
  });
  renderFeedback(state.feedbackItems);
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
    submit.textContent = "导入中...";
    try {
      const data = await requestJson(url, { method: "POST", body: formData });
      showResult(data, title);
      input.value = "";
      form.querySelector(".file-picker span").textContent = "选择 Excel";
      state.studentPage = 1;
      await loadStudents();
      await loadFeedback();
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
      state.studentPage = 1;
      await loadStudents().catch((error) => showToast(error.message));
    }, 240);
  });

  document.querySelectorAll('[data-status]').forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll('[data-status]').forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.status = button.dataset.status || "all";
      state.studentPage = 1;
      await loadStudents().catch((error) => showToast(error.message));
    });
  });
}

function bindPagination() {
  $("#students-prev").addEventListener("click", () => {
    if (state.studentPage <= 1) return;
    state.studentPage -= 1;
    renderStudents(state.students);
  });

  $("#students-next").addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(state.students.length / state.studentsPerPage));
    if (state.studentPage >= totalPages) return;
    state.studentPage += 1;
    renderStudents(state.students);
  });
}

function bindResultPanel() {
  $("#result-panel").addEventListener("click", (event) => {
    if (event.target.closest(".close-result")) {
      $("#result-panel").classList.add("hidden");
    }
  });
}

async function updateFeedback(feedbackId, payload, successMessage) {
  const data = await requestJson("/api/feedback/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: feedbackId, ...payload }),
  });
  showToast(data.backup ? `${successMessage}，已自动备份` : successMessage);
}

async function runFeedbackBatch(action) {
  const ids = Array.from(state.selectedFeedbackIds);
  if (!ids.length) {
    showToast("请先选择反馈记录");
    return;
  }
  if (action === "delete" && state.feedbackStatus !== "resolved") {
    showToast("只能删除已处理反馈记录");
    return;
  }

  const actionText = action === "grant" ? "补票" : action === "reject" ? "驳回" : "删除";
  const confirmed = window.confirm(`确认批量${actionText} ${ids.length} 条反馈吗？系统会自动备份数据库。`);
  if (!confirmed) return;

  const buttons = ["#feedback-batch-grant", "#feedback-batch-reject", "#feedback-batch-delete"]
    .map((selector) => $(selector))
    .filter(Boolean);
  buttons.forEach((button) => {
    button.disabled = true;
  });

  const failures = [];
  for (const id of ids) {
    try {
      await requestJson("/api/feedback/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(id), action }),
      });
    } catch (error) {
      failures.push(`${id}: ${error.message}`);
    }
  }

  state.selectedFeedbackIds.clear();
  if (action !== "delete") {
    state.feedbackStatus = "resolved";
    document.querySelectorAll('[data-feedback-status]').forEach((item) => {
      item.classList.toggle("active", item.dataset.feedbackStatus === "resolved");
    });
  }
  await loadStudents();
  await loadFeedback();

  if (failures.length) {
    showToast(`批量${actionText}完成，${failures.length} 条失败`);
  } else {
    showToast(`批量${actionText}完成`);
  }
}

function bindFeedbackActions() {
  $("#refresh-feedback").addEventListener("click", async () => {
    try {
      state.selectedFeedbackIds.clear();
      await loadFeedback();
      showToast("反馈记录已刷新");
    } catch (error) {
      showToast(error.message);
    }
  });

  document.querySelectorAll('[data-feedback-status]').forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll('[data-feedback-status]').forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.feedbackStatus = button.dataset.feedbackStatus || "pending";
      state.selectedFeedbackIds.clear();
      renderFeedback(state.feedbackItems);
    });
  });

  $("#feedback-batch-grant").addEventListener("click", () => {
    runFeedbackBatch("grant").catch((error) => showToast(error.message));
  });
  $("#feedback-batch-reject").addEventListener("click", () => {
    runFeedbackBatch("reject").catch((error) => showToast(error.message));
  });
  $("#feedback-batch-delete").addEventListener("click", () => {
    runFeedbackBatch("delete").catch((error) => showToast(error.message));
  });

  $("#feedback-body").addEventListener("change", (event) => {
    const selectAll = event.target.closest("#feedback-select-all");
    if (selectAll) {
      filterFeedback(state.feedbackItems).forEach((item) => {
        const id = String(item.id);
        if (selectAll.checked) {
          state.selectedFeedbackIds.add(id);
        } else {
          state.selectedFeedbackIds.delete(id);
        }
      });
      renderFeedback(state.feedbackItems);
      return;
    }

    const checkbox = event.target.closest(".feedback-select");
    if (!checkbox) return;
    event.stopPropagation();
    const id = String(checkbox.value);
    if (checkbox.checked) {
      state.selectedFeedbackIds.add(id);
    } else {
      state.selectedFeedbackIds.delete(id);
    }
    updateFeedbackBatchState();
  });

  $("#feedback-body").addEventListener("click", async (event) => {
    if (event.target.closest(".feedback-select")) {
      event.stopPropagation();
      return;
    }

    const button = event.target.closest(".grant-ticket, .reject-feedback, .delete-feedback");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();

    const card = event.target.closest("[data-feedback-id]");
    if (!card) return;

    const feedbackId = Number(card.dataset.feedbackId);
    const adminNote = card.querySelector(".feedback-note").value.trim();
    const action = button.classList.contains("grant-ticket")
      ? "grant"
      : button.classList.contains("reject-feedback")
        ? "reject"
        : "delete";
    const confirmed = window.confirm(
      action === "grant"
        ? "确认给该学生补加这次讲座票吗？系统会自动备份数据库。"
        : action === "reject"
          ? "确认驳回这条反馈吗？如果这条反馈此前补过票，系统会撤回这张票并自动备份数据库。"
          : "确认删除这条已处理反馈记录吗？学生端仍可看到处理结果通知。",
    );
    if (!confirmed) return;

    button.disabled = true;
    try {
      await updateFeedback(
        feedbackId,
        {
          action,
          admin_note: adminNote,
        },
        action === "grant" ? "已补票并移入已处理" : action === "reject" ? "已驳回并移入已处理" : "已删除反馈记录",
      );
      if (action !== "delete") {
        state.feedbackStatus = "resolved";
        document.querySelectorAll('[data-feedback-status]').forEach((item) => {
          item.classList.toggle("active", item.dataset.feedbackStatus === "resolved");
        });
      }
      state.selectedFeedbackIds.delete(String(feedbackId));
      await loadStudents();
      await loadFeedback();
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  });

  $("#feedback-body").addEventListener(
    "blur",
    async (event) => {
      if (!event.target.classList.contains("feedback-note")) return;

      const card = event.target.closest("[data-feedback-id]");
      if (!card) return;

      const feedbackId = Number(card.dataset.feedbackId);
      const adminNote = event.target.value.trim();

      try {
        await updateFeedback(feedbackId, { action: "note", admin_note: adminNote }, "管理员备注已保存");
        await loadFeedback();
      } catch (error) {
        showToast(error.message);
      }
    },
    true,
  );
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
  bindPagination();
  bindResultPanel();
  bindFeedbackActions();
  bindLogout();
  await loadStudents();
  await loadFeedback();
}

init().catch((error) => showToast(error.message));
