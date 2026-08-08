#!/usr/bin/env node
/**
 * Thin npx entry for Hermes Chrome companion install.
 *
 *   npx hermes-chrome-companion
 *   npx hermes-chrome-companion install
 *   npx hermes-chrome-companion install --dev
 *   npx hermes-chrome-companion doctor
 *   npx hermes-chrome-companion help
 *
 * This package does NOT ship the Python bridge. It downloads/clones from
 * GitHub and invokes scripts/install.sh (or Windows install-windows.ps1).
 */
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const https = require("https");
const http = require("http");
const { URL } = require("url");

const REPO = "leaf76/hermes-chrome";
const RAW_INSTALL =
  process.env.HERMES_CHROME_INSTALL_URL ||
  `https://raw.githubusercontent.com/${REPO}/main/scripts/install.sh`;
const WIN_INSTALL_RAW =
  process.env.HERMES_CHROME_WIN_INSTALL_URL ||
  `https://raw.githubusercontent.com/${REPO}/main/scripts/install-windows.ps1`;
const CWS =
  "https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa";

function log(msg) {
  console.error(`[hermes-chrome-companion] ${msg}`);
}

function usage() {
  console.log(`Hermes Chrome companion (npm thin installer)

Usage:
  npx hermes-chrome-companion [install] [flags]
  npx hermes-chrome-companion doctor
  npx hermes-chrome-companion help

Flags (install):
  --dev          Use a local git clone if found (HERMES_CHROME_ROOT or cwd)
  --skip-mcp     Skip MCP client registration
  --uninstall    Uninstall services (see install.sh)

This is NOT an npm reimplementation of the bridge.
Runtime installs to ~/.hermes/hermes-chrome via GitHub scripts.

After install:
  hermes-chrome doctor
  hermes-chrome --json ping
  Extension: ${CWS}
`);
}

function which(cmd) {
  if (process.platform === "win32") {
    const r = spawnSync("where", [cmd], { encoding: "utf8" });
    return r.status === 0 ? r.stdout.split(/\r?\n/)[0].trim() : "";
  }
  const r = spawnSync("/bin/sh", ["-c", 'command -v "$1"', "sh", cmd], {
    encoding: "utf8",
  });
  return r.status === 0 ? r.stdout.trim() : "";
}

function findLocalRoot() {
  if (process.env.HERMES_CHROME_ROOT) {
    const p = path.resolve(process.env.HERMES_CHROME_ROOT);
    if (fs.existsSync(path.join(p, "scripts", "install.sh"))) return p;
  }
  // Walk up from cwd
  let dir = process.cwd();
  for (let i = 0; i < 6; i++) {
    if (
      fs.existsSync(path.join(dir, "bridge.py")) &&
      fs.existsSync(path.join(dir, "scripts", "install.sh"))
    ) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  const homeInstall = path.join(os.homedir(), ".hermes", "hermes-chrome");
  if (fs.existsSync(path.join(homeInstall, "scripts", "install.sh"))) {
    return homeInstall;
  }
  return null;
}

function run(cmd, args, opts = {}) {
  log(`$ ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, {
    stdio: "inherit",
    env: process.env,
    ...opts,
  });
  if (r.error) throw r.error;
  return r.status ?? 1;
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "http:" ? http : https;
    const req = lib.get(u, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        download(res.headers.location, dest).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        res.resume();
        return;
      }
      const f = fs.createWriteStream(dest);
      res.pipe(f);
      f.on("finish", () => f.close(() => resolve(dest)));
      f.on("error", reject);
    });
    req.on("error", reject);
  });
}

async function installUnix(flags) {
  const local = flags.dev ? findLocalRoot() : null;
  if (local) {
    log(`using local tree: ${local}`);
    const args = [path.join(local, "scripts", "install.sh")];
    if (flags.dev) args.push("--dev");
    if (flags.skipMcp) args.push("--skip-mcp");
    if (flags.uninstall) args.push("--uninstall");
    return run("bash", args);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-chrome-"));
  const script = path.join(tmp, "install.sh");
  log(`downloading install.sh → ${script}`);
  await download(RAW_INSTALL, script);
  fs.chmodSync(script, 0o755);
  const args = [script];
  if (flags.skipMcp) args.push("--skip-mcp");
  if (flags.uninstall) args.push("--uninstall");
  if (flags.dev) args.push("--dev");
  return run("bash", args);
}

async function installWindows(flags) {
  const local = flags.dev ? findLocalRoot() : null;
  let ps1;
  if (local && fs.existsSync(path.join(local, "scripts", "install-windows.ps1"))) {
    ps1 = path.join(local, "scripts", "install-windows.ps1");
    log(`using local: ${ps1}`);
  } else {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-chrome-"));
    ps1 = path.join(tmp, "install-windows.ps1");
    log(`downloading install-windows.ps1 → ${ps1}`);
    await download(WIN_INSTALL_RAW, ps1);
  }
  const args = [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    ps1,
  ];
  if (flags.skipMcp) args.push("-SkipGrok");
  if (flags.uninstall) args.push("-Uninstall");
  return run("powershell.exe", args);
}

function doctor() {
  const candidates = [
    path.join(os.homedir(), ".local", "bin", "hermes-chrome"),
    which("hermes-chrome"),
    path.join(os.homedir(), ".hermes", "hermes-chrome", "scripts", "hermes-chrome.sh"),
  ].filter(Boolean);

  for (const c of candidates) {
    if (c && fs.existsSync(c)) {
      if (process.platform === "win32") {
        return run("bash", [c, "doctor"]);
      }
      return run(c, ["doctor"]);
    }
  }
  log("companion CLI not found — run: npx hermes-chrome-companion install");
  return 1;
}

function parseArgs(argv) {
  const flags = { dev: false, skipMcp: false, uninstall: false };
  const rest = [];
  for (const a of argv) {
    if (a === "--dev") flags.dev = true;
    else if (a === "--skip-mcp" || a === "--skip-grok") flags.skipMcp = true;
    else if (a === "--uninstall") flags.uninstall = true;
    else if (a === "-h" || a === "--help") rest.push("help");
    else rest.push(a);
  }
  let cmd = rest[0] || "install";
  if (cmd.startsWith("-")) {
    // flags only → install
    cmd = "install";
  }
  return { cmd, flags, rest: rest.slice(1) };
}

async function main() {
  const { cmd, flags } = parseArgs(process.argv.slice(2));
  if (cmd === "help" || cmd === "-h" || cmd === "--help") {
    usage();
    return 0;
  }
  if (cmd === "doctor") {
    return doctor();
  }
  if (cmd === "install" || cmd === "i") {
    log("Hermes Chrome companion install (thin npm wrapper → GitHub scripts)");
    log("Runtime is Python + local bridge — not an npm-native server.");
    if (process.platform === "win32") {
      return await installWindows(flags);
    }
    return await installUnix(flags);
  }
  log(`unknown command: ${cmd}`);
  usage();
  return 1;
}

main()
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
