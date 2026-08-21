const form = document.getElementById("login-form");
const submit = document.getElementById("login-submit");
const message = document.getElementById("login-message");

if (new URLSearchParams(window.location.search).has("expired")) {
  message.textContent = "Tu sesión venció. Ingresá nuevamente.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  submit.disabled = true;
  submit.textContent = "Ingresando…";
  message.textContent = "";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(data.entries())),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo iniciar sesión.");
    window.location.replace("/");
  } catch (error) {
    message.textContent = error.message;
    document.getElementById("password").select();
  } finally {
    submit.disabled = false;
    submit.textContent = "Ingresar";
  }
});
