#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const root = __dirname;
const out = path.join(root, ".vercel", "output");

function rmrf(dir) {
  fs.rmSync(dir, { recursive: true, force: true });
}

function mkdirp(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function write(file, contents) {
  mkdirp(path.dirname(file));
  fs.writeFileSync(file, contents);
}

function bundleApi(name) {
  const src = fs.readFileSync(path.join(root, "api", `${name}.js`), "utf8");
  if (!src.includes('require("../lib/oauth")')) {
    throw new Error(`${name}.js must require("../lib/oauth")`);
  }
  const funcDir = path.join(out, "functions", "api", `${name}.func`);
  mkdirp(funcDir);
  fs.writeFileSync(
    path.join(funcDir, "index.js"),
    src.replaceAll('require("../lib/oauth")', 'require("./oauth")'),
  );
  fs.copyFileSync(path.join(root, "lib", "oauth.js"), path.join(funcDir, "oauth.js"));
  write(
    path.join(funcDir, ".vc-config.json"),
    `${JSON.stringify(
      {
        runtime: "nodejs20.x",
        handler: "index.js",
        launcherType: "Nodejs",
        shouldAddHelpers: true,
      },
      null,
      2,
    )}\n`,
  );
}

rmrf(out);
mkdirp(path.join(out, "static"));
fs.copyFileSync(path.join(root, "index.html"), path.join(out, "static", "index.html"));
bundleApi("start");
bundleApi("callback");
bundleApi("revoke");
write(
  path.join(out, "config.json"),
  `${JSON.stringify(
    {
      version: 3,
      routes: [
        { handle: "filesystem" },
        { src: "/api/start", dest: "/api/start" },
        { src: "/api/callback", dest: "/api/callback" },
        { src: "/api/revoke", dest: "/api/revoke" },
      ],
    },
    null,
    2,
  )}\n`,
);

console.log("Wrote .vercel/output (static + /api/start + /api/callback + /api/revoke)");
