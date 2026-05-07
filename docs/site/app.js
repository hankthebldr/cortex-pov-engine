// CortexSim landing — fetches the latest release from the GitHub API and
// renders the version, image ref, and asset table. Falls back gracefully if
// the API is rate-limited or the page is opened with no network.

(function () {
  "use strict";

  const repo = window.CS_REPO || { owner: "__GH_OWNER__", name: "__GH_REPO__" };
  const owner = repo.owner;
  const name = repo.name;
  const apiUrl = `https://api.github.com/repos/${owner}/${name}/releases/latest`;

  document.getElementById("cs-year").textContent = new Date().getFullYear();
  document.getElementById("cs-image-ref").textContent =
    `ghcr.io/${owner.toLowerCase()}/cortexsim:latest`;

  // Copy-to-clipboard
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

  function fmtBytes(n) {
    if (!Number.isFinite(n)) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: "numeric", month: "short", day: "numeric",
      });
    } catch (_) {
      return iso;
    }
  }

  function showFallback(reason) {
    document.getElementById("cs-version").textContent = "no published release yet";
    document.getElementById("cs-published").textContent = "—";
    const meta = document.getElementById("cs-release-meta");
    meta.textContent = reason ||
      "Once the first release is tagged, downloadable artifacts will appear here.";
    const tbody = document.querySelector("#cs-asset-table tbody");
    tbody.innerHTML =
      `<tr><td colspan="3" class="cs-empty">No release artifacts yet.</td></tr>`;
  }

  function renderAssets(release) {
    const tag = release.tag_name || "latest";
    document.getElementById("cs-version").textContent = tag;
    document.getElementById("cs-published").textContent = fmtDate(release.published_at);
    document.getElementById("cs-image-ref").textContent =
      `ghcr.io/${owner.toLowerCase()}/cortexsim:${tag}`;

    const link = document.getElementById("cs-latest-link");
    if (release.html_url) link.href = release.html_url;

    const meta = document.getElementById("cs-release-meta");
    meta.innerHTML =
      `Latest tag <code>${tag}</code> &middot; ` +
      `published ${fmtDate(release.published_at)}. ` +
      `<a href="${release.html_url}" rel="noopener">View on GitHub &rarr;</a>`;

    const tbody = document.querySelector("#cs-asset-table tbody");
    const assets = (release.assets || []).slice().sort((a, b) =>
      a.name.localeCompare(b.name)
    );
    if (assets.length === 0) {
      tbody.innerHTML =
        `<tr><td colspan="3" class="cs-empty">Release has no uploaded assets.</td></tr>`;
      return;
    }
    const manifestAsset = assets.find((a) => a.name === "manifest.json");
    const rows = assets.map((a) => {
      const url = a.browser_download_url;
      return (
        `<tr>` +
        `<td><a href="${url}" rel="noopener">${a.name}</a></td>` +
        `<td>${fmtBytes(a.size)}</td>` +
        `<td><code data-asset="${a.name}">—</code></td>` +
        `</tr>`
      );
    });
    tbody.innerHTML = rows.join("");

    // Best-effort SHA-256 enrichment from manifest.json. Failure is silent —
    // SHA256SUMS is still linked in the table.
    if (manifestAsset) {
      fetch(manifestAsset.browser_download_url, { mode: "cors" })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((manifest) => {
          (manifest.artifacts || []).forEach((art) => {
            const cell = document.querySelector(
              `code[data-asset="${CSS.escape(art.name)}"]`
            );
            if (cell && art.sha256) {
              cell.textContent = art.sha256.slice(0, 16) + "…";
              cell.title = art.sha256;
            }
          });
        })
        .catch(() => { /* leave dashes */ });
    }
  }

  fetch(apiUrl, { headers: { Accept: "application/vnd.github+json" } })
    .then((r) => {
      if (r.status === 404) throw new Error("no-release");
      if (!r.ok) throw new Error(`http ${r.status}`);
      return r.json();
    })
    .then(renderAssets)
    .catch((err) => {
      const msg = err && err.message === "no-release"
        ? "No published release yet. Tag <code>v0.1.0</code> to cut the first one."
        : null;
      showFallback(msg);
    });
})();
