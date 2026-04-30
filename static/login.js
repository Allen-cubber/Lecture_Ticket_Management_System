const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = document.querySelector("#admin-password").value;
  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = "登录中";
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "登录失败");
    }
    const next = new URLSearchParams(window.location.search).get("next") || "/";
    window.location.href = next.startsWith("/") ? next : "/";
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "登录";
  }
});
