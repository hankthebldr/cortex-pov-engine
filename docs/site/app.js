// CortexSim landing — progressive enhancement only.
//
// This page deliberately makes NO network calls. It previously fetched
// /releases/latest from the GitHub API to render a version, a publish date
// and an artifact table. That repo has never been tagged, so the fetch
// always 404'd and three sections rendered as permanent empty scaffolding
// while the install one-liners they described pointed at URLs that 404 too.
//
// A landing page for a build-from-source project should state that, not
// degrade gracefully into implying otherwise. If a release is ever cut,
// re-add the fetch here alongside a Downloads section — don't ship the
// section ahead of the artifact.

(function () {
  "use strict";

  const yearEl = document.getElementById("cs-year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // Copy-to-clipboard for every code block that offers a button.
  document.querySelectorAll(".cs-copy").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const target = document.getElementById(btn.dataset.copy);
      if (!target) return;
      const text = target.innerText.trim();
      try {
        await navigator.clipboard.writeText(text);
        const original = btn.textContent;
        btn.textContent = "copied";
        btn.classList.add("is-copied");
        setTimeout(() => {
          btn.textContent = original;
          btn.classList.remove("is-copied");
        }, 1400);
      } catch (e) {
        console.warn("clipboard write failed", e);
      }
    });
  });
})();
