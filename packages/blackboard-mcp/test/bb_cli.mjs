#!/usr/bin/env node
/**
 * CLI wrapper for blackboard MCP server.
 * Usage: node bb_cli.mjs <tool_name> '<json_args>'
 * Example: node bb_cli.mjs bb_create '{"task":"Analyze X"}'
 */
import { spawn } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { existsSync, readFileSync, writeFileSync } from "fs";
import { tmpdir } from "os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVER_PATH = join(__dirname, "..", "dist", "index.js");
const SESSION_FILE = join(tmpdir(), "bb_cli_session.json");

const toolName = process.argv[2];
const argsStr = process.argv[3] || "{}";

if (!toolName) {
  console.log("Usage: node bb_cli.mjs <tool_name> '<json_args>'");
  console.log("Tools: bb_create, bb_add_document, bb_add_entries, bb_add_signal,");
  console.log("       bb_get_state, bb_mark_read, bb_search, bb_convergence,");
  console.log("       bb_synthesis, bb_iterate, bb_snapshot, bb_list");
  process.exit(0);
}

let args;
try {
  args = JSON.parse(argsStr);
} catch (e) {
  console.error("Invalid JSON args:", e.message);
  process.exit(1);
}

const proc = spawn("node", [SERVER_PATH], { stdio: ["pipe", "pipe", "pipe"] });
let output = "";
proc.stdout.on("data", d => output += d.toString());

const messages = [
  { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "bb_cli", version: "0.1" } } },
  { jsonrpc: "2.0", method: "notifications/initialized" },
  { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: toolName, arguments: args } },
];

for (const msg of messages) {
  proc.stdin.write(JSON.stringify(msg) + "\n");
}

setTimeout(() => {
  proc.kill();
  const lines = output.split("\n").filter(Boolean);
  const resultLine = lines.find(l => l.includes('"id":2'));
  if (resultLine) {
    try {
      const parsed = JSON.parse(resultLine);
      const text = parsed.result?.content?.[0]?.text;
      if (text) {
        try {
          const data = JSON.parse(text);
          console.log(JSON.stringify(data, null, 2));
        } catch {
          console.log(text);
        }
      } else {
        console.log(JSON.stringify(parsed.result, null, 2));
      }
    } catch {
      console.log(resultLine);
    }
  } else {
    console.error("No response received");
    process.exit(1);
  }
}, 2000);
