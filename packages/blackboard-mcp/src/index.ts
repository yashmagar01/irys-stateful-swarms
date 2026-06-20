#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { randomUUID } from "crypto";
import { mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync, renameSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

// ── Types ──────────────────────────────────────────────────────────────

interface EntrySource {
  document: string;
  section: string;
  evidence: string;
}

interface Entry {
  id: string;
  type: "observation" | "analysis" | "calculation" | "strategy" | "gap";
  content: string;
  source: EntrySource | null;
  confidence: number;
  status: "active" | "disputed" | "superseded" | "retracted";
  tags: string[];
  created_by: string;
  iteration: number;
  opens_questions: string[];
  supports_entries: string[];
  contradicts_entries: string[];
  supersedes_entries: string[];
  addresses_signals: string[];
}

interface Signal {
  id: string;
  type: "question" | "convergence_gap" | "contradiction_resolution" | "source_gap";
  content: string;
  origin_entry: string;
  priority: "low" | "medium" | "high" | "critical";
  status: "open" | "addressed" | "expired";
  addressed_by: string | null;
  iteration_created: number;
}

interface Document {
  id: string;
  name: string;
  text: string;
  sections: string[];
  sections_read: string[];
  read_status: "unread" | "partially_read" | "fully_read";
}

interface Blackboard {
  id: string;
  task: string;
  iteration: number;
  entries: Entry[];
  signals: Signal[];
  documents: Document[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ── State ──────────────────────────────────────────────────────────────

// Store in project directory (.blackboard/) so state persists across sessions.
// Falls back to temp if CLAUDE_PROJECT_DIR is not set.
const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const STORE_ROOT = join(PROJECT_DIR, ".blackboard");
const blackboards = new Map<string, Blackboard>();
let entryCounter = 0;
let signalCounter = 0;

function genEntryId(): string {
  return `e${++entryCounter}`;
}

function genSignalId(): string {
  return `s${++signalCounter}`;
}

function stateDir(bbId: string): string {
  const d = join(STORE_ROOT, bbId);
  mkdirSync(d, { recursive: true });
  return d;
}

function saveState(bb: Blackboard): void {
  const d = stateDir(bb.id);
  bb.updated_at = new Date().toISOString();
  const tmp = join(d, "state.tmp");
  const target = join(d, "state.json");
  writeFileSync(tmp, JSON.stringify(bb, null, 2), "utf-8");
  renameSync(tmp, target);
}

function loadState(bbId: string): Blackboard | null {
  if (blackboards.has(bbId)) return blackboards.get(bbId)!;
  const f = join(stateDir(bbId), "state.json");
  if (!existsSync(f)) return null;
  const bb: Blackboard = JSON.parse(readFileSync(f, "utf-8"));
  blackboards.set(bbId, bb);
  // advance counters past loaded IDs
  for (const e of bb.entries) {
    const n = parseInt(e.id.slice(1));
    if (n > entryCounter) entryCounter = n;
  }
  for (const s of bb.signals) {
    const n = parseInt(s.id.slice(1));
    if (n > signalCounter) signalCounter = n;
  }
  return bb;
}

// ── Blackboard Logic ───────────────────────────────────────────────────

function bbSummary(bb: Blackboard) {
  const active = bb.entries.filter(e => e.status === "active");
  const typeCounts: Record<string, number> = {};
  for (const e of active) typeCounts[e.type] = (typeCounts[e.type] || 0) + 1;
  const openSigs = bb.signals.filter(s => s.status === "open");
  return {
    iteration: bb.iteration,
    total_entries: bb.entries.length,
    active_entries: active.length,
    entry_types: typeCounts,
    open_signals: openSigs.length,
    critical_signals: openSigs.filter(s => s.priority === "critical").length,
    documents: bb.documents.length,
    documents_unread: bb.documents.filter(d => d.read_status === "unread").length,
  };
}

function addEntriesToBb(bb: Blackboard, rawEntries: unknown[], workerId: string): { newEntries: Entry[]; newSignals: Signal[] } {
  const validTypes = new Set(["observation", "analysis", "calculation", "strategy", "gap"]);
  const newEntries: Entry[] = [];
  const newSignals: Signal[] = [];

  for (const raw of rawEntries) {
    if (typeof raw !== "object" || raw === null) continue;
    const ed = raw as Record<string, unknown>;

    let entryType = String(ed.type || "observation");
    if (!validTypes.has(entryType)) entryType = "observation";

    let conf = typeof ed.confidence === "number" ? ed.confidence : 0.7;
    conf = Math.max(0, Math.min(1, conf));

    let source: EntrySource | null = null;
    if (ed.source && typeof ed.source === "object") {
      const s = ed.source as Record<string, unknown>;
      source = {
        document: String(s.document || ""),
        section: String(s.section || ""),
        evidence: String(s.evidence || ""),
      };
    }

    const strList = (v: unknown): string[] => {
      if (!Array.isArray(v)) return [];
      return v.filter(x => typeof x === "string") as string[];
    };

    const entry: Entry = {
      id: genEntryId(),
      type: entryType as Entry["type"],
      content: String(ed.content || ""),
      source,
      confidence: conf,
      status: "active",
      tags: strList(ed.tags),
      created_by: workerId,
      iteration: bb.iteration,
      opens_questions: strList(ed.opens_questions),
      supports_entries: strList(ed.supports_entries),
      contradicts_entries: strList(ed.contradicts_entries),
      supersedes_entries: strList(ed.supersedes_entries),
      addresses_signals: strList(ed.addresses_signals),
    };

    bb.entries.push(entry);
    newEntries.push(entry);

    // auto-create signals from opens_questions
    for (const q of entry.opens_questions) {
      if (!q.trim()) continue;
      // dedup: skip if a similar open signal already exists
      const exists = bb.signals.some(
        s => s.status === "open" && s.content.toLowerCase() === q.toLowerCase()
      );
      if (!exists) {
        const sig: Signal = {
          id: genSignalId(),
          type: "question",
          content: q,
          origin_entry: entry.id,
          priority: "medium",
          status: "open",
          addressed_by: null,
          iteration_created: bb.iteration,
        };
        bb.signals.push(sig);
        newSignals.push(sig);
      }
    }

    // handle contradiction marking + confidence decay
    for (const cId of entry.contradicts_entries) {
      const target = bb.entries.find(e => e.id === cId);
      if (target && target.status === "active") {
        target.status = "disputed";
        target.confidence = Math.max(0, target.confidence - 0.15);
      }
    }

    // handle signal resolution
    for (const sId of entry.addresses_signals) {
      const sig = bb.signals.find(s => s.id === sId);
      if (sig && sig.status === "open") {
        sig.status = "addressed";
        sig.addressed_by = entry.id;
      }
    }

    // handle supersedes
    for (const sId of entry.supersedes_entries) {
      const target = bb.entries.find(e => e.id === sId);
      if (target) target.status = "superseded";
    }

    // confidence propagation: boost supported entries
    for (const sId of entry.supports_entries) {
      const target = bb.entries.find(e => e.id === sId);
      if (target && target.status === "active") {
        target.confidence = Math.min(1, target.confidence + 0.05);
      }
    }
  }

  return { newEntries, newSignals };
}

function entryDict(e: Entry) {
  const d: Record<string, unknown> = {
    id: e.id, type: e.type, content: e.content,
    confidence: e.confidence, status: e.status,
    tags: e.tags, created_by: e.created_by, iteration: e.iteration,
  };
  if (e.source) d.source = e.source;
  if (e.opens_questions.length) d.opens_questions = e.opens_questions;
  if (e.supports_entries.length) d.supports_entries = e.supports_entries;
  if (e.contradicts_entries.length) d.contradicts_entries = e.contradicts_entries;
  if (e.supersedes_entries.length) d.supersedes_entries = e.supersedes_entries;
  if (e.addresses_signals.length) d.addresses_signals = e.addresses_signals;
  return d;
}

function signalDict(s: Signal) {
  return {
    id: s.id, type: s.type, content: s.content,
    origin_entry: s.origin_entry, priority: s.priority,
    status: s.status, addressed_by: s.addressed_by,
    iteration_created: s.iteration_created,
  };
}

function docDict(d: Document) {
  return {
    id: d.id, name: d.name, sections: d.sections,
    sections_read: d.sections_read, read_status: d.read_status,
    text_length: d.text.length,
  };
}

// ── MCP Server ─────────────────────────────────────────────────────────

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

Entry types: observation (source-grounded fact), analysis (interpretation), calculation (derived work), strategy (framing decision), gap (missing evidence).

The blackboard automatically tracks contradictions, creates signals from open questions, and computes convergence. Your answer should be assembled from blackboard state — this ensures provenance and surfaces contradictions.`;

const server = new McpServer({
  name: "blackboard",
  version: "0.1.0",
}, {
  instructions: INSTRUCTIONS,
});

// ── Tool: bb_create ────────────────────────────────────────────────────

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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          blackboard_id: bbId,
          task,
          summary: bbSummary(bb),
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
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_add_document ──────────────────────────────────────────────

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
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    const docId = `doc_${bb.documents.length + 1}`;
    // auto-detect markdown sections if none provided
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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          doc_id: docId,
          name,
          text_length: text.length,
          sections: doc.sections,
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_add_entries ───────────────────────────────────────────────

server.tool(
  "bb_add_entries",
  `Add structured findings to the blackboard. The blackboard automatically: creates signals from opens_questions, marks contradictions as disputed, resolves signals from addresses_signals, and propagates confidence.`,
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    entries: z.union([
      z.array(z.object({
        type: z.enum(["observation", "analysis", "calculation", "strategy", "gap"]).optional().describe("Entry type (default: observation)."),
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
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    let rawEntries: unknown[];
    if (typeof entries === "string") {
      try {
        rawEntries = JSON.parse(entries);
      } catch (e) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Invalid entries JSON: ${e}` }) }] };
      }
      if (!Array.isArray(rawEntries)) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "entries must be a JSON array" }) }] };
      }
    } else {
      rawEntries = entries;
    }

    const { newEntries, newSignals } = addEntriesToBb(bb, rawEntries, worker_id || "agent");
    saveState(bb);

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          created_entries: newEntries.map(entryDict),
          new_signals: newSignals.map(signalDict),
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_add_signal ────────────────────────────────────────────────

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
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    // dedup
    const exists = bb.signals.some(
      s => s.status === "open" && s.content.toLowerCase() === content.toLowerCase()
    );
    if (exists) {
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ deduped: true, summary: bbSummary(bb) }),
        }],
      };
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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          signal: signalDict(sig),
          deduped: false,
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_get_state ─────────────────────────────────────────────────

server.tool(
  "bb_get_state",
  "Inspect the current blackboard state: entries, signals, documents.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    entry_status: z.string().optional().describe("Comma-separated entry statuses to include (default: active,disputed)."),
    max_entries: z.number().optional().describe("Maximum entries to return (default: 100)."),
  },
  async ({ blackboard_id, entry_status, max_entries }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    const statuses = new Set((entry_status || "active,disputed").split(","));
    const filtered = bb.entries.filter(e => statuses.has(e.status)).slice(0, max_entries || 100);
    const openSigs = bb.signals.filter(s => s.status === "open");

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          blackboard_id: bb.id,
          task: bb.task,
          summary: bbSummary(bb),
          entries: filtered.map(entryDict),
          signals: openSigs.map(signalDict),
          documents: bb.documents.map(docDict),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_mark_read ─────────────────────────────────────────────────

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
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    const doc = bb.documents.find(d => d.id === doc_id);
    if (!doc) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Document not found" }) }] };

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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          doc_id: doc.id,
          read_status: doc.read_status,
          sections_read: doc.sections_read,
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_search ────────────────────────────────────────────────────

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
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

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
      return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Invalid search pattern" }) }] };
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
    } catch { /* regex error already handled above */ }

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({ query, document_results: results, entry_results: entryMatches, total_documents: results.length, total_entries: entryMatches.length }),
      }],
    };
  }
);

// ── Tool: bb_convergence ───────────────────────────────────────────────

server.tool(
  "bb_convergence",
  "Check if the blackboard analysis is complete. Returns blockers: critical signals, disputed entries, unread documents. Do NOT present a final answer while blockers exist.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
  },
  async ({ blackboard_id }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          converged: blockers.length === 0,
          blockers,
          critical_signals: critical.map(signalDict),
          high_signals: high.map(signalDict),
          disputed_entries: disputed.map(entryDict),
          unread_documents: unread.map(docDict),
          partially_read_documents: partial.map(docDict),
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_synthesis ─────────────────────────────────────────────────

server.tool(
  "bb_synthesis",
  "Get everything needed to synthesize a final answer. Returns must-include entries, disputed entries, and open signals. Base your answer on THIS, not on memory.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
  },
  async ({ blackboard_id }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    const active = bb.entries.filter(e => e.status === "active");
    const highConf = active.filter(e => e.confidence >= 0.6);
    const disputed = bb.entries.filter(e => e.status === "disputed");
    const openSigs = bb.signals.filter(s => s.status === "open");

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          task: bb.task,
          must_include_entries: highConf.map(entryDict),
          disputed_entries: disputed.map(entryDict),
          open_signals: openSigs.map(signalDict),
          documents: bb.documents.map(d => ({ name: d.name, read_status: d.read_status })),
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_iterate ───────────────────────────────────────────────────

server.tool(
  "bb_iterate",
  "Advance the blackboard iteration. Optionally expires stale low/medium signals.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    expire_stale: z.boolean().optional().describe("Expire stale low/medium signals (default: true)."),
  },
  async ({ blackboard_id, expire_stale }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

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

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          iteration: bb.iteration,
          expired_signals: expired.map(signalDict),
          summary: bbSummary(bb),
        }, null, 2),
      }],
    };
  }
);

// ── Tool: bb_snapshot ──────────────────────────────────────────────────

server.tool(
  "bb_snapshot",
  "Save a named snapshot of the current blackboard state.",
  {
    blackboard_id: z.string().describe("The blackboard ID."),
    label: z.string().optional().describe("Label for the snapshot."),
  },
  async ({ blackboard_id, label }) => {
    const bb = loadState(blackboard_id);
    if (!bb) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Blackboard not found" }) }] };

    const d = join(stateDir(blackboard_id), "snapshots");
    mkdirSync(d, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const safeLabel = (label || "").replace(/[^\w-]/g, "_").slice(0, 50);
    const suffix = safeLabel ? `_${safeLabel}` : "";
    const path = join(d, `${ts}${suffix}.json`);
    writeFileSync(path, JSON.stringify(bb, null, 2), "utf-8");

    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({ path, summary: bbSummary(bb) }),
      }],
    };
  }
);

// ── Tool: bb_list ──────────────────────────────────────────────────────

server.tool(
  "bb_list",
  "List all blackboards in this project. Call this FIRST to check for existing analysis you can build on.",
  {},
  async () => {
    const results: Array<Record<string, unknown>> = [];
    if (existsSync(STORE_ROOT)) {
      for (const name of readdirSync(STORE_ROOT)) {
        const f = join(STORE_ROOT, name, "state.json");
        if (existsSync(f)) {
          try {
            const data = JSON.parse(readFileSync(f, "utf-8"));
            const entries = data.entries || [];
            const signals = data.signals || [];
            const docs = data.documents || [];
            const typeCounts: Record<string, number> = {};
            for (const e of entries) {
              if (e.status === "active") typeCounts[e.type] = (typeCounts[e.type] || 0) + 1;
            }
            results.push({
              blackboard_id: name,
              task: (data.task || "").slice(0, 200),
              entries: entries.length,
              open_signals: signals.filter((s: Record<string, string>) => s.status === "open").length,
              documents: docs.map((d: Record<string, string>) => d.name).slice(0, 10),
              entry_types: typeCounts,
              iteration: data.iteration || 0,
              updated_at: data.updated_at || "",
            });
          } catch { /* skip corrupt */ }
        }
      }
    }
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          blackboards: results,
          hint: results.length > 0
            ? "Existing blackboards found. Call bb_get_state on a relevant one to see its entries and continue the analysis."
            : "No existing blackboards. Call bb_create to start a new analysis.",
        }, null, 2),
      }],
    };
  }
);

// ── Start ──────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch(err => {
  console.error("Server error:", err);
  process.exit(1);
});
