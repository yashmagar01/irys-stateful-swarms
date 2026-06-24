#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { randomUUID } from "crypto";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

import type { Blackboard, Document, Signal } from "./types.js";
import { blackboards, saveState, loadState, listAllBlackboards, stateDir, genSignalId } from "./store.js";
import { bbSummary, addEntriesToBb, entryDict, signalDict, docDict } from "./logic.js";
import {
  renderCreate, renderList, renderAddDocument, renderAddEntries,
  renderAddSignal, renderGetState, renderMarkRead, renderSearch,
  renderConvergence, renderSynthesis, renderIterate, renderSnapshot,
  renderExportConfirmation, renderDiagramConfirmation, convergenceScore,
} from "./render/fmt.js";
import { renderMermaidDiagram } from "./render/mermaid.js";
import { renderBlackboardExportHtml } from "./render/html.js";

// ── Output Mode ───────────────────────────────────────────────────────

const OUTPUT_MODE = process.env.BLACKBOARD_MCP_OUTPUT || "rich";

function toolResult(jsonPayload: unknown, richText: string) {
  const text = OUTPUT_MODE === "json" ? JSON.stringify(jsonPayload, null, 2) : richText;
  return { content: [{ type: "text" as const, text }] };
}

function errorResult(msg: string) {
  return { content: [{ type: "text" as const, text: JSON.stringify({ error: msg }) }] };
}

// ── MCP Server ────────────────────────────────────────────────────────

const INSTRUCTIONS = `Blackboard MCP provides persistent structured reasoning for complex tasks.

Blackboards persist across sessions in the project's .blackboard/ directory. When you start working, ALWAYS call bb_list first to check for existing blackboards — previous sessions may have built relevant knowledge you can read and extend instead of starting from scratch.

USE THIS when the task involves:
- Analyzing, comparing, or synthesizing information from multiple sources
- Multi-step reasoning where intermediate findings matter
- Any task where accuracy, provenance, or contradiction tracking matter
- Building understanding of a codebase, system, or domain over time
- Continuing work started in a previous session

DO NOT USE for simple questions with obvious answers.

Workflow — new analysis:
1. Call bb_list to check for existing blackboards on this topic.
2. If none exist, call bb_create with your task description.
3. Read documents with your file tools, then call bb_add_document to register text.
4. Call bb_add_entries to record findings (observation, analysis, calculation, strategy, gap).
5. Connect entries: supports_entries, contradicts_entries, opens_questions, addresses_signals.
6. Call bb_convergence before your final answer — resolve blockers first.
7. Call bb_synthesis to assemble the answer from blackboard state.

Workflow — continuing from existing blackboard:
1. Call bb_list and find the relevant blackboard.
2. Call bb_get_state to see what was already recorded.
3. Build on existing entries — add new findings, resolve open signals, address gaps.
4. Call bb_convergence and bb_synthesis when ready.

Visualization tools:
- Call bb_diagram to generate a Mermaid graph of the reasoning topology.
- Call bb_export to create a self-contained interactive HTML visualization.

Entry types: observation (source-grounded fact), analysis (interpretation), calculation (derived work), strategy (framing decision), gap (missing evidence).

Every entry SHOULD include a short label (3-6 words) — this is what appears on graph nodes and cross-reference buttons in the visualization. Good labels: "Revenue growth decelerating", "Auth middleware race condition", "Missing test coverage for edge cases". Bad labels: "e4", "finding 1", "observation".

The blackboard automatically tracks contradictions, creates signals from open questions, and computes convergence. Your answer should be assembled from blackboard state — this ensures provenance and surfaces contradictions.`;

const server = new McpServer({
  name: "blackboard",
  version: "0.2.0",
}, {
  instructions: INSTRUCTIONS,
});

// ── Tool: bb_create ───────────────────────────────────────────────────

server.tool(
  "bb_create",
  "Create a new blackboard for structured reasoning. Returns the blackboard ID for subsequent calls.",
  {
    task: z.string().describe("What you want to analyze, investigate, or reason about."),
    metadata: z.string().optional().describe("Optional JSON metadata string."),
  },
  async ({ task, metadata }) => {
    const bbId = randomUUID().slice(0, 8);
    let meta: Record<string, unknown> = {};
    if (metadata) {
      try { meta = JSON.parse(metadata); } catch { /* ignore */ }
    }
    const bb: Blackboard = {
      id: bbId,
      task,
      iteration: 0,
      entries: [],
      signals: [],
      documents: [],
      metadata: meta,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    blackboards.set(bbId, bb);
    saveState(bb);

    const summary = bbSummary(bb);
    const payload = {
      blackboard_id: bbId,
      task,
      summary,
      entry_template: {
        type: "observation",
        content: "Concise source-grounded finding.",
        source: { document: "doc_id", section: "section name", evidence: "short quote" },
        confidence: 0.8,
        tags: [],
        opens_questions: [],
        supports_entries: [],
        contradicts_entries: [],
        addresses_signals: [],
      },
      next_steps: [
        "Read your source documents and call bb_add_document to register them.",
        "Call bb_add_entries with typed findings as you read and reason.",
        "Use opens_questions to flag unresolved issues (they become signals).",
        "Call bb_convergence before finalizing — resolve critical blockers first.",
        "Call bb_synthesis to assemble your final answer from blackboard state.",
      ],
    };

    return toolResult(payload, renderCreate(bbId, task, summary));
  }
);

// ── Tool: bb_add_document ─────────────────────────────────────────────

server.tool(
  "bb_add_document",
  "Register a document's text on the blackboard for provenance tracking. Read the document yourself first, then pass the text here.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    name: z.string().describe("Document name or file path."),
    text: z.string().describe("The full document text."),
    sections: z.array(z.string()).optional().describe("Section headings found in the document."),
  },
  async ({ blackboard_id, name, text, sections }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const docId = `doc_${bb.documents.length + 1}`;
    let detectedSections = sections || [];
    if (detectedSections.length === 0) {
      const headerPattern = /^#{1,4}\s+(.+)$/gm;
      let match;
      while ((match = headerPattern.exec(text)) !== null) {
        detectedSections.push(match[1].trim());
      }
    }
    const doc: Document = {
      id: docId,
      name,
      text,
      sections: detectedSections,
      sections_read: [],
      read_status: "unread",
    };
    bb.documents.push(doc);
    saveState(bb);

    const payload = {
      doc_id: docId,
      name,
      text_length: text.length,
      sections: doc.sections,
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderAddDocument(docId, name, text.length, doc.sections, bb.documents, bbSummary(bb), bb));
  }
);

// ── Tool: bb_add_entries ──────────────────────────────────────────────

server.tool(
  "bb_add_entries",
  `Add structured findings to the blackboard. The blackboard automatically: creates signals from opens_questions, marks contradictions as disputed, resolves signals from addresses_signals, and propagates confidence.`,
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    entries: z.union([
      z.array(z.object({
        type: z.enum(["observation", "analysis", "calculation", "strategy", "gap"]).optional().describe("Entry type (default: observation)."),
        label: z.string().optional().describe("Short human-readable label (3-6 words). Shown on graph nodes and cross-references. Example: 'Revenue growth slowing', 'Memory leak in worker pool'."),
        content: z.string().describe("The finding text."),
        source: z.object({
          document: z.string().describe("Document ID."),
          section: z.string().optional().describe("Section heading."),
          evidence: z.string().optional().describe("Short quote or source locator."),
        }).optional().describe("Source provenance."),
        confidence: z.number().min(0).max(1).optional().describe("Confidence 0.0-1.0 (default: 0.7)."),
        tags: z.array(z.string()).optional(),
        opens_questions: z.array(z.string()).optional().describe("Questions raised — become signals."),
        supports_entries: z.array(z.string()).optional().describe("Entry IDs this supports."),
        contradicts_entries: z.array(z.string()).optional().describe("Entry IDs this contradicts."),
        supersedes_entries: z.array(z.string()).optional().describe("Entry IDs this supersedes."),
        addresses_signals: z.array(z.string()).optional().describe("Signal IDs this resolves."),
      })),
      z.string().describe("JSON array of entry objects (legacy string format)."),
    ]).describe("Array of entry objects, or a JSON string encoding such an array."),
    worker_id: z.string().optional().describe("Who created these entries (default: agent)."),
  },
  async ({ blackboard_id, entries, worker_id }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    let rawEntries: unknown[];
    if (typeof entries === "string") {
      try {
        rawEntries = JSON.parse(entries);
      } catch (e) {
        return errorResult(`Invalid entries JSON: ${e}`);
      }
      if (!Array.isArray(rawEntries)) {
        return errorResult("entries must be a JSON array");
      }
    } else {
      rawEntries = entries;
    }

    const { newEntries, newSignals } = addEntriesToBb(bb, rawEntries, worker_id || "agent");
    saveState(bb);

    const payload = {
      created_entries: newEntries.map(entryDict),
      new_signals: newSignals.map(signalDict),
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderAddEntries(newEntries, newSignals, bb));
  }
);

// ── Tool: bb_add_signal ───────────────────────────────────────────────

server.tool(
  "bb_add_signal",
  "Add a signal (question, gap, or issue) to the blackboard. Signals are deduplicated.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    signal_type: z.enum(["question", "convergence_gap", "contradiction_resolution", "source_gap"]).describe("Type of signal."),
    content: z.string().describe("Description of the question or gap."),
    priority: z.enum(["low", "medium", "high", "critical"]).optional().describe("Signal priority (default: medium)."),
    origin_entry: z.string().optional().describe("Entry ID that raised this signal."),
  },
  async ({ blackboard_id, signal_type, content, priority, origin_entry }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const exists = bb.signals.some(
      s => s.status === "open" && s.content.toLowerCase() === content.toLowerCase()
    );
    if (exists) {
      const payload = { deduped: true, summary: bbSummary(bb) };
      const fakeSig: Signal = { id: "", type: signal_type, content, origin_entry: origin_entry || "", priority: priority || "medium", status: "open", addressed_by: null, iteration_created: bb.iteration };
      return toolResult(payload, renderAddSignal(fakeSig, true, bb));
    }

    const sig: Signal = {
      id: genSignalId(),
      type: signal_type,
      content,
      origin_entry: origin_entry || "",
      priority: priority || "medium",
      status: "open",
      addressed_by: null,
      iteration_created: bb.iteration,
    };
    bb.signals.push(sig);
    saveState(bb);

    const payload = {
      signal: signalDict(sig),
      deduped: false,
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderAddSignal(sig, false, bb));
  }
);

// ── Tool: bb_get_state ────────────────────────────────────────────────

server.tool(
  "bb_get_state",
  "Inspect the current blackboard state: entries, signals, documents, and reasoning graph.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    entry_status: z.string().optional().describe("Comma-separated entry statuses to include (default: active,disputed)."),
    max_entries: z.number().optional().describe("Maximum entries to return (default: 100)."),
  },
  async ({ blackboard_id, entry_status, max_entries }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const statuses = new Set((entry_status || "active,disputed").split(","));
    const filtered = bb.entries.filter(e => statuses.has(e.status)).slice(0, max_entries || 100);
    const openSigs = bb.signals.filter(s => s.status === "open");

    const payload = {
      blackboard_id: bb.id,
      task: bb.task,
      summary: bbSummary(bb),
      entries: filtered.map(entryDict),
      signals: openSigs.map(signalDict),
      documents: bb.documents.map(docDict),
    };

    return toolResult(payload, renderGetState(bb, filtered, openSigs));
  }
);

// ── Tool: bb_mark_read ────────────────────────────────────────────────

server.tool(
  "bb_mark_read",
  "Mark document sections as read. Call this after you've read parts of a document to track coverage.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    doc_id: z.string().describe("Document ID."),
    sections: z.array(z.string()).optional().describe("Section names to mark as read. If omitted, marks entire document as read."),
  },
  async ({ blackboard_id, doc_id, sections }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const doc = bb.documents.find(d => d.id === doc_id);
    if (!doc) return errorResult("Document not found");

    if (sections) {
      for (const s of sections) {
        if (!doc.sections_read.includes(s)) doc.sections_read.push(s);
      }
      if (doc.sections.length > 0 && doc.sections.every(s => doc.sections_read.includes(s))) {
        doc.read_status = "fully_read";
      } else if (doc.sections_read.length > 0) {
        doc.read_status = "partially_read";
      }
    } else {
      doc.sections_read = [...doc.sections];
      doc.read_status = "fully_read";
    }

    saveState(bb);

    const payload = {
      doc_id: doc.id,
      read_status: doc.read_status,
      sections_read: doc.sections_read,
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderMarkRead(doc, sections, bb));
  }
);

// ── Tool: bb_search ───────────────────────────────────────────────────

server.tool(
  "bb_search",
  "Search documents AND entries on the blackboard for a pattern (case-insensitive).",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    query: z.string().describe("Text pattern to search for."),
    max_results: z.number().optional().describe("Maximum results (default: 20)."),
    context_chars: z.number().optional().describe("Characters of context around each match (default: 500)."),
  },
  async ({ blackboard_id, query, max_results, context_chars }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const maxR = max_results || 20;
    const ctxChars = context_chars || 500;
    const results: Array<{ doc_id: string; name: string; start: number; end: number; snippet: string }> = [];

    try {
      const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      for (const doc of bb.documents) {
        if (results.length >= maxR) break;
        let match;
        while ((match = pattern.exec(doc.text)) !== null && results.length < maxR) {
          const start = Math.max(0, match.index - Math.floor(ctxChars / 2));
          const end = Math.min(doc.text.length, match.index + match[0].length + Math.floor(ctxChars / 2));
          results.push({
            doc_id: doc.id, name: doc.name,
            start, end,
            snippet: doc.text.slice(start, end),
          });
        }
      }
    } catch {
      return errorResult("Invalid search pattern");
    }

    const entryMatches: Array<{ entry_id: string; type: string; content: string; confidence: number; status: string }> = [];
    try {
      const ep = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      for (const e of bb.entries) {
        if (entryMatches.length >= maxR) break;
        if (ep.test(e.content)) {
          entryMatches.push({ entry_id: e.id, type: e.type, content: e.content, confidence: e.confidence, status: e.status });
        }
      }
    } catch { /* already handled */ }

    const payload = { query, document_results: results, entry_results: entryMatches, total_documents: results.length, total_entries: entryMatches.length };

    return toolResult(payload, renderSearch(query, results, entryMatches));
  }
);

// ── Tool: bb_convergence ──────────────────────────────────────────────

server.tool(
  "bb_convergence",
  "Check if the blackboard analysis is complete. Returns blockers: critical signals, disputed entries, unread documents. Do NOT present a final answer while blockers exist.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
  },
  async ({ blackboard_id }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const openSigs = bb.signals.filter(s => s.status === "open");
    const critical = openSigs.filter(s => s.priority === "critical");
    const high = openSigs.filter(s => s.priority === "high");
    const disputed = bb.entries.filter(e => e.status === "disputed");
    const unread = bb.documents.filter(d => d.read_status === "unread");
    const partial = bb.documents.filter(d => d.read_status === "partially_read");

    const blockers: string[] = [];
    if (critical.length) blockers.push(`${critical.length} critical signal(s) unresolved`);
    if (disputed.length) blockers.push(`${disputed.length} disputed entry/entries`);
    if (unread.length) blockers.push(`${unread.length} document(s) completely unread`);

    const payload = {
      converged: blockers.length === 0,
      blockers,
      critical_signals: critical.map(signalDict),
      high_signals: high.map(signalDict),
      disputed_entries: disputed.map(entryDict),
      unread_documents: unread.map(docDict),
      partially_read_documents: partial.map(docDict),
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderConvergence(bb));
  }
);

// ── Tool: bb_synthesis ────────────────────────────────────────────────

server.tool(
  "bb_synthesis",
  "Get everything needed to synthesize a final answer. Returns must-include entries, disputed entries, and open signals. Base your answer on THIS, not on memory.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
  },
  async ({ blackboard_id }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const active = bb.entries.filter(e => e.status === "active");
    const highConf = active.filter(e => e.confidence >= 0.6);
    const disputed = bb.entries.filter(e => e.status === "disputed");
    const openSigs = bb.signals.filter(s => s.status === "open");

    const payload = {
      task: bb.task,
      must_include_entries: highConf.map(entryDict),
      disputed_entries: disputed.map(entryDict),
      open_signals: openSigs.map(signalDict),
      documents: bb.documents.map(d => ({ name: d.name, read_status: d.read_status })),
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderSynthesis(bb));
  }
);

// ── Tool: bb_iterate ──────────────────────────────────────────────────

server.tool(
  "bb_iterate",
  "Advance the blackboard iteration. Optionally expires stale low/medium signals.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    expire_stale: z.boolean().optional().describe("Expire stale low/medium signals (default: true)."),
  },
  async ({ blackboard_id, expire_stale }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const prevIteration = bb.iteration;
    bb.iteration++;
    const expired: Signal[] = [];
    if (expire_stale !== false) {
      const threshold = bb.iteration - 3;
      for (const s of bb.signals) {
        if (s.status === "open" && (s.priority === "low" || s.priority === "medium") && s.iteration_created < threshold) {
          s.status = "expired";
          expired.push(s);
        }
      }
    }
    saveState(bb);

    const payload = {
      iteration: bb.iteration,
      expired_signals: expired.map(signalDict),
      summary: bbSummary(bb),
    };

    return toolResult(payload, renderIterate(bb, prevIteration, expired));
  }
);

// ── Tool: bb_snapshot ─────────────────────────────────────────────────

server.tool(
  "bb_snapshot",
  "Save a named snapshot of the current blackboard state.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    label: z.string().optional().describe("Label for the snapshot."),
  },
  async ({ blackboard_id, label }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const d = join(stateDir(blackboard_id), "snapshots");
    mkdirSync(d, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const safeLabel = (label || "").replace(/[^\w-]/g, "_").slice(0, 50);
    const suffix = safeLabel ? `_${safeLabel}` : "";
    const path = join(d, `${ts}${suffix}.json`);
    writeFileSync(path, JSON.stringify(bb, null, 2), "utf-8");

    const payload = { path, summary: bbSummary(bb) };

    return toolResult(payload, renderSnapshot(path, bb));
  }
);

// ── Tool: bb_list ─────────────────────────────────────────────────────

server.tool(
  "bb_list",
  "List all blackboards in this project. Call this FIRST to check for existing analysis you can build on.",
  {},
  async () => {
    const results = listAllBlackboards();

    const payload = {
      blackboards: results,
      hint: results.length > 0
        ? "Existing blackboards found. Call bb_get_state on a relevant one to see its entries and continue the analysis."
        : "No existing blackboards. Call bb_create to start a new analysis.",
    };

    return toolResult(payload, renderList(results));
  }
);

// ── Tool: bb_diagram ──────────────────────────────────────────────────

server.tool(
  "bb_diagram",
  "Generate a Mermaid diagram of the blackboard reasoning graph. Copy the output into any Mermaid renderer for an interactive visual.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    entry_status: z.string().optional().describe("Comma-separated entry statuses to include (default: active,disputed)."),
    max_entries: z.number().optional().describe("Maximum entries in the diagram (default: 100)."),
    include_signals: z.boolean().optional().describe("Include signal nodes (default: true)."),
    direction: z.enum(["TD", "LR"]).optional().describe("Graph direction: TD (top-down) or LR (left-right). Default: TD."),
  },
  async ({ blackboard_id, entry_status, max_entries, include_signals, direction }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const statusFilter = new Set((entry_status || "active,disputed").split(","));
    const { mermaid, counts } = renderMermaidDiagram(bb, {
      maxEntries: max_entries,
      includeSignals: include_signals,
      direction: direction as "TD" | "LR" | undefined,
      entryStatus: statusFilter,
    });

    const payload = {
      blackboard_id: bb.id,
      nodes: counts.nodes,
      edges: counts.edges,
      mermaid,
    };

    const richText = renderDiagramConfirmation(counts.nodes, counts.edges) + "\n\n```mermaid\n" + mermaid + "\n```";

    return toolResult(payload, richText);
  }
);

// ── Tool: bb_export ───────────────────────────────────────────────────

server.tool(
  "bb_export",
  "Export an interactive HTML visualization of the blackboard. Creates a self-contained file you can open in any browser — no server required.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    path: z.string().optional().describe("Output file path. Default: .blackboard/<id>/exports/<timestamp>.html"),
  },
  async ({ blackboard_id, path }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return errorResult("Blackboard not found");

    const { html, counts } = renderBlackboardExportHtml(bb);

    const exportDir = join(stateDir(blackboard_id), "exports");
    mkdirSync(exportDir, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const outPath = path || join(exportDir, `${ts}-blackboard.html`);
    writeFileSync(outPath, html, "utf-8");

    const payload = {
      path: outPath,
      blackboard_id: bb.id,
      nodes: counts.nodes,
      edges: counts.edges,
    };

    return toolResult(payload, renderExportConfirmation(outPath, bb, counts.nodes, counts.edges));
  }
);

// ── Start ─────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch(err => {
  console.error("Server error:", err);
  process.exit(1);
});
