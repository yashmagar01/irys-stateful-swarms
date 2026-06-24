import { spawn } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVER_PATH = join(__dirname, "..", "dist", "index.js");

let pass = 0;
let fail = 0;

function check(label, condition) {
  if (condition) {
    console.log(`  PASS: ${label}`);
    pass++;
  } else {
    console.log(`  FAIL: ${label}`);
    fail++;
  }
}

async function runServer(messages) {
  return new Promise((resolve, reject) => {
    const proc = spawn("node", [SERVER_PATH], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, BLACKBOARD_MCP_OUTPUT: "json" } });
    let output = "";
    proc.stdout.on("data", d => output += d.toString());
    proc.stderr.on("data", () => {});

    for (const msg of messages) {
      proc.stdin.write(JSON.stringify(msg) + "\n");
    }

    setTimeout(() => {
      proc.kill();
      const responses = output.split("\n").filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);
      resolve(responses);
    }, 2000);
  });
}

function findById(responses, id) {
  return responses.find(r => r.id === id);
}

function parseToolResult(response) {
  if (!response?.result?.content?.[0]?.text) return null;
  try { return JSON.parse(response.result.content[0].text); } catch { return null; }
}

async function main() {
  console.log("=== Blackboard MCP Server - Full Workflow Test ===\n");

  // Phase 1: Create blackboard and add document
  console.log("Phase 1: Create + Add Document");
  const phase1 = await runServer([
    { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "test", version: "0.1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} },
    { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "bb_create", arguments: { task: "Compare Project Alpha and Beta performance reports" } } },
  ]);

  const toolsList = findById(phase1, 2);
  check("tools/list returns 14 tools", toolsList?.result?.tools?.length === 14);

  const toolNames = (toolsList?.result?.tools || []).map(t => t.name);
  check("has bb_create", toolNames.includes("bb_create"));
  check("has bb_add_entries", toolNames.includes("bb_add_entries"));
  check("has bb_convergence", toolNames.includes("bb_convergence"));
  check("has bb_synthesis", toolNames.includes("bb_synthesis"));

  const createResult = parseToolResult(findById(phase1, 3));
  check("bb_create returns blackboard_id", !!createResult?.blackboard_id);
  check("bb_create returns entry_template", !!createResult?.entry_template);
  check("bb_create returns next_steps", Array.isArray(createResult?.next_steps));

  const bbId = createResult?.blackboard_id;
  console.log(`  Blackboard ID: ${bbId}\n`);

  // Phase 2: Add document + entries + signals
  console.log("Phase 2: Add Document + Entries + Signals");
  const phase2 = await runServer([
    { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "test", version: "0.1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 10, method: "tools/call", params: { name: "bb_add_document", arguments: { blackboard_id: bbId, name: "Alpha Report", text: "Project Alpha achieved 94.2% uptime in Q1. Cost was $2.3M, up 15%.", sections: ["Summary", "Performance", "Cost"] } } },
    { jsonrpc: "2.0", id: 11, method: "tools/call", params: { name: "bb_add_document", arguments: { blackboard_id: bbId, name: "Beta Report", text: "Project Beta maintained 99.95% uptime. Cost was $1.8M, down 10%.", sections: ["Summary", "Performance", "Cost"] } } },
    { jsonrpc: "2.0", id: 12, method: "tools/call", params: { name: "bb_add_entries", arguments: { blackboard_id: bbId, entries: JSON.stringify([
      { type: "observation", content: "Alpha Q1 uptime was 94.2%, below SLA", source: { document: "doc_1", section: "Summary", evidence: "94.2% uptime" }, confidence: 0.95, opens_questions: ["What caused the low uptime?"] },
      { type: "observation", content: "Beta Q1 uptime was 99.95%, all planned maintenance", source: { document: "doc_2", section: "Summary", evidence: "99.95% uptime" }, confidence: 0.95 },
      { type: "observation", content: "Alpha cost $2.3M (+15%)", source: { document: "doc_1", section: "Cost", evidence: "$2.3M" }, confidence: 0.95 },
      { type: "observation", content: "Beta cost $1.8M (-10%)", source: { document: "doc_2", section: "Cost", evidence: "$1.8M" }, confidence: 0.95 },
    ]) } } },
    { jsonrpc: "2.0", id: 13, method: "tools/call", params: { name: "bb_add_signal", arguments: { blackboard_id: bbId, signal_type: "question", content: "Which system is more cost-effective per uptime point?", priority: "high" } } },
  ]);

  const addDoc1 = parseToolResult(findById(phase2, 10));
  check("bb_add_document returns doc_id", addDoc1?.doc_id === "doc_1");
  check("bb_add_document tracks text_length", addDoc1?.text_length > 0);

  const addDoc2 = parseToolResult(findById(phase2, 11));
  check("bb_add_document second doc", addDoc2?.doc_id === "doc_2");

  const addEntries = parseToolResult(findById(phase2, 12));
  check("bb_add_entries creates 4 entries", addEntries?.created_entries?.length === 4);
  check("bb_add_entries auto-creates signal from opens_questions", addEntries?.new_signals?.length >= 1);
  check("bb_add_entries summary shows 4 active", addEntries?.summary?.active_entries === 4);

  const addSignal = parseToolResult(findById(phase2, 13));
  check("bb_add_signal creates signal", !!addSignal?.signal?.id);
  check("bb_add_signal not deduped", addSignal?.deduped === false);
  console.log("");

  // Phase 3: Contradictions, convergence, resolution
  console.log("Phase 3: Contradictions + Convergence + Resolution");
  const phase3 = await runServer([
    { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "test", version: "0.1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    // Add contradicting entry
    { jsonrpc: "2.0", id: 20, method: "tools/call", params: { name: "bb_add_entries", arguments: { blackboard_id: bbId, entries: JSON.stringify([
      { type: "analysis", content: "Alpha's low uptime makes it unreliable", confidence: 0.7, contradicts_entries: ["e2"], supports_entries: ["e1"] },
    ]) } } },
    // Check convergence (should have blockers)
    { jsonrpc: "2.0", id: 21, method: "tools/call", params: { name: "bb_convergence", arguments: { blackboard_id: bbId } } },
    // Mark documents as read
    { jsonrpc: "2.0", id: 22, method: "tools/call", params: { name: "bb_mark_read", arguments: { blackboard_id: bbId, doc_id: "doc_1" } } },
    { jsonrpc: "2.0", id: 23, method: "tools/call", params: { name: "bb_mark_read", arguments: { blackboard_id: bbId, doc_id: "doc_2" } } },
    // Resolve signal
    { jsonrpc: "2.0", id: 24, method: "tools/call", params: { name: "bb_add_entries", arguments: { blackboard_id: bbId, entries: JSON.stringify([
      { type: "analysis", content: "Alpha's March incident was a one-time configuration error, now remediated", confidence: 0.85, addresses_signals: ["s1", "s2"] },
      { type: "calculation", content: "Cost per 9 of uptime: Alpha=$24.4K/9, Beta=$18K/9. Beta is 26% more cost-effective.", confidence: 0.9 },
    ]) } } },
    // Search
    { jsonrpc: "2.0", id: 25, method: "tools/call", params: { name: "bb_search", arguments: { blackboard_id: bbId, query: "uptime" } } },
    // Get state
    { jsonrpc: "2.0", id: 26, method: "tools/call", params: { name: "bb_get_state", arguments: { blackboard_id: bbId } } },
  ]);

  const contradiction = parseToolResult(findById(phase3, 20));
  check("Contradiction entry created", contradiction?.created_entries?.length === 1);

  const convergence1 = parseToolResult(findById(phase3, 21));
  check("Convergence reports NOT converged", convergence1?.converged === false);
  check("Convergence has blockers", convergence1?.blockers?.length > 0);
  // Check disputed entries in get_state (later in pipeline, avoids async timing)
  const stateForDisputed = parseToolResult(findById(phase3, 26));
  const disputedInState = stateForDisputed?.entries?.some((e) => e.status === "disputed");
  check("Disputed entries exist in state", disputedInState === true);

  const markRead = parseToolResult(findById(phase3, 22));
  check("Mark read works", markRead?.read_status === "fully_read");

  const resolve = parseToolResult(findById(phase3, 24));
  check("Resolution entries created", resolve?.created_entries?.length === 2);

  const search = parseToolResult(findById(phase3, 25));
  check("Search finds matches", (search?.total_documents > 0) || (search?.total_entries > 0));

  const state = parseToolResult(findById(phase3, 26));
  check("Get state returns entries", state?.entries?.length > 0);
  check("Get state returns documents", state?.documents?.length === 2);
  console.log("");

  // Phase 4: Iterate, snapshot, synthesis, list
  console.log("Phase 4: Iterate + Snapshot + Synthesis + List");
  const phase4 = await runServer([
    { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "test", version: "0.1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 30, method: "tools/call", params: { name: "bb_iterate", arguments: { blackboard_id: bbId } } },
    { jsonrpc: "2.0", id: 31, method: "tools/call", params: { name: "bb_snapshot", arguments: { blackboard_id: bbId, label: "test-snapshot" } } },
    { jsonrpc: "2.0", id: 32, method: "tools/call", params: { name: "bb_synthesis", arguments: { blackboard_id: bbId } } },
    { jsonrpc: "2.0", id: 33, method: "tools/call", params: { name: "bb_list", arguments: {} } },
    // Check convergence again after resolving
    { jsonrpc: "2.0", id: 34, method: "tools/call", params: { name: "bb_convergence", arguments: { blackboard_id: bbId } } },
  ]);

  const iterate = parseToolResult(findById(phase4, 30));
  check("Iterate advances to iteration 1", iterate?.iteration === 1);

  const snapshot = parseToolResult(findById(phase4, 31));
  check("Snapshot returns path", !!snapshot?.path);

  const synthesis = parseToolResult(findById(phase4, 32));
  check("Synthesis returns must_include_entries", synthesis?.must_include_entries?.length > 0);
  check("Synthesis returns task", !!synthesis?.task);
  check("Synthesis includes high-confidence entries", synthesis?.must_include_entries?.some(e => e.confidence >= 0.6));

  const list = parseToolResult(findById(phase4, 33));
  check("List returns blackboards", list?.blackboards?.length > 0);
  check("List includes our blackboard", list?.blackboards?.some(b => b.blackboard_id === bbId));

  const convergence2 = parseToolResult(findById(phase4, 34));
  check("Convergence after resolution - disputed still blocks", convergence2?.converged === false || convergence2?.disputed_entries?.length > 0);

  // Summary
  console.log(`\n=== Results: ${pass} passed, ${fail} failed ===`);
  process.exit(fail > 0 ? 1 : 0);
}

main().catch(err => {
  console.error("Test error:", err);
  process.exit(1);
});
