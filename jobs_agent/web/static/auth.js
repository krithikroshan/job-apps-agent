// Shared by login.html and signup.html — which one it's on is read from the
// form's data-mode attribute, so the same script posts to /api/login or
// /api/signup.
const form = document.getElementById("auth-form");
const msg = document.getElementById("auth-msg");

// Supabase's confirmation email lands here with the outcome in the URL
// fragment. Say what happened, then drop the fragment so the tokens in it
// don't linger in the address bar or history.
const landed = new URLSearchParams(location.hash.slice(1));
if (landed.get("error_description")) {
  msg.textContent = landed.get("error_description");
  msg.classList.add("err");
} else if (landed.get("type") === "signup") {
  msg.textContent = "Email confirmed. Log in to get started.";
}
if (location.hash) history.replaceState(null, "", location.pathname + location.search);

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "";
  msg.classList.remove("err");

  const mode = form.dataset.mode;
  const email = form.email.value.trim();
  const password = form.password.value;
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;

  try {
    const res = await fetch(`/api/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      msg.textContent = data.error || "Something went wrong.";
      msg.classList.add("err");
      return;
    }
    if (data.message) {
      msg.textContent = data.message;
      return;
    }
    location.href = "/";
  } catch (err) {
    msg.textContent = "Could not reach the server.";
    msg.classList.add("err");
  } finally {
    btn.disabled = false;
  }
});
