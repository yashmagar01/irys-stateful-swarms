import type { Blackboard, Entry, Signal, Document, BbSummary } from "../types.js";

// ── Theme Constants ───────────────────────────────────────────────────

const TYPE_BADGE: Record<string, string> = {
  observation: "OBS", analysis: "ANL", calculation: "CAL",
  strategy: "STR", gap: "GAP",
};

const STATUS_ICON: Record<string, string> = {
  active: "✓", disputed: "✕", superseded: "↻", retracted: "↻",
};

const SIG_STATUS_ICON: Record<string, string> = {
  open: "⚠", addressed: "✓", expired: "↻",
};

const EDGE_SYM = {
  supports: "⇢", contradicts: "⇄", supersedes: "↻",
  addresses: "✓", opens: "?",
};

// ── Formatting Primitives ─────────────────────────────────────────────

export function confidenceDots(c: number): string {
  const filled = Math.round(c * 5);
  return "●".repeat(filled) + "○".repeat(5 - filled);
}

export function progressBar(value: number, max: number, width = 10): string {
  const pct = max > 0 ? value / max : 0;
  const filled = Math.round(pct * width);
  return "▰".repeat(filled) + "▱".repeat(width - filled);
}

function pctBar(pct: number, width = 10): string {
  const clamped = Math.max(0, Math.min(100, pct));
  const filled = Math.round((clamped / 100) * width);
  return "▰".repeat(filled) + "▱".repeat(width - filled);
}

function badge(type: string): string {
  return `[${TYPE_BADGE[type] || type.slice(0, 3).toUpperCase()}]`;
}

function sIcon(status: string): string {
  return STATUS_ICON[status] || "?";
}

function sigIcon(status: string): string {
  return SIG_STATUS_ICON[status] || "?";
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function edgeStr(e: Entry): string {
  const parts: string[] = [];
  if (e.supports_entries.length) parts.push(`${EDGE_SYM.supports} ${e.supports_entries.join(" ")}`);
  if (e.contradicts_entries.length) parts.push(`${EDGE_SYM.contradicts} ${e.contradicts_entries.join(" ")}`);
  if (e.supersedes_entries.length) parts.push(`${EDGE_SYM.supersedes} ${e.supersedes_entries.join(" ")}`);
  if (e.addresses_signals.length) parts.push(`${EDGE_SYM.addresses} ${e.addresses_signals.join(" ")}`);
  if (e.opens_questions.length) parts.push(`${EDGE_SYM.opens} ${e.opens_questions.length > 1 ? e.opens_questions.length + " questions" : "signal"}`);
  return parts.join("   ");
}

// ── Box Drawing ───────────────────────────────────────────────────────

const BOX_W = 76;

function boxTop(title: string, tool: string): string {
  const inner = ` ${title} `;
  const toolTag = ` ${tool} ─`;
  const fill = BOX_W - 3 - inner.length - toolTag.length;
  return `╭─${inner}${"─".repeat(Math.max(0, fill))}${toolTag}╮`;
}

function boxMid(title: string): string {
  const inner = ` ${title} `;
  const fill = BOX_W - 3 - inner.length;
  return `├─${inner}${"─".repeat(Math.max(0, fill))}┤`;
}

function boxBot(): string {
  return `╰${"─".repeat(BOX_W - 1)}╯`;
}

function boxLine(text: string): string {
  const padded = text.length < BOX_W - 4 ? text + " ".repeat(BOX_W - 4 - text.length) : text;
  return `│ ${padded} │`;
}

function freeLines(lines: string[]): string {
  return lines.map(l => `│ ${l}`).join("\n");
}

// ── Convergence Score ─────────────────────────────────────────────────

export function convergenceScore(bb: Blackboard): { score: number; status: string; blockers: string[] } {
  const openSigs = bb.signals.filter(s => s.status === "open");
  const critical = openSigs.filter(s => s.priority === "critical").length;
  const high = openSigs.filter(s => s.priority === "high").length;
  const disputed = bb.entries.filter(e => e.status === "disputed").length;
  const unread = bb.documents.filter(d => d.read_status === "unread").length;

  const blockers: string[] = [];
  if (critical) blockers.push(`critical ${critical === 1 ? "signal" : "signals"} ${critical}`);
  if (disputed) blockers.push(`disputed ${disputed === 1 ? "entry" : "entries"} ${disputed}`);
  if (unread) blockers.push(`unread ${unread === 1 ? "doc" : "docs"} ${unread}`);

  const score = Math.max(0, Math.min(100, 100 - critical * 22 - high * 9 - disputed * 7 - unread * 11));
  const status = blockers.length === 0 ? "✓ converged" : "⚠ blocked";

  return { score, status, blockers };
}

function miniConvergence(bb: Blackboard): string {
  const { score, status, blockers } = convergenceScore(bb);
  const openSigs = bb.signals.filter(s => s.status === "open");
  const totalSections = bb.documents.reduce((s, d) => s + d.sections.length, 0);
  const readSections = bb.documents.reduce((s, d) => s + d.sections_read.length, 0);
  const lines = [
    boxMid("Mini Convergence"),
    boxLine(`Overall      ${pctBar(score)} ${score}%  ${status}`),
    boxLine(`Evidence     ${progressBar(readSections, totalSections || 1)} ${bb.documents.length} docs, ${readSections}/${totalSections} sections read`),
    boxLine(`Signals      ${progressBar(openSigs.length === 0 ? 1 : 0, 1)} ${openSigs.length} open / ${bb.signals.length} total`),
    boxLine(`Disputes     ${progressBar(bb.entries.filter(e => e.status === "disputed").length === 0 ? 1 : 0, 1)} ${bb.entries.filter(e => e.status === "disputed").length} disputed`),
  ];
  if (blockers.length) {
    lines.push(boxLine(`Blockers     ${blockers.join(", ")}`));
  }
  return lines.join("\n");
}

// ── Per-Tool Renderers ────────────────────────────────────────────────

export function renderCreate(bbId: string, task: string, summary: BbSummary): string {
  const lines = [
    boxTop("Blackboard MCP", "bb_create"),
    boxLine(`✓ Created blackboard ${bbId}`),
    boxLine(`Task: ${truncate(task, BOX_W - 12)}`),
    boxMid("State"),
    boxLine(`Iteration        0`),
    boxLine(`Entries          0        [OBS] 0  [ANL] 0  [CAL] 0  [STR] 0  [GAP] 0`),
    boxLine(`Documents        0        unread 0`),
    boxLine(`Signals          0        critical 0  high 0  medium 0  low 0`),
    boxLine(`Convergence      ${pctBar(0)} 0%  ⚠ no evidence yet`),
    boxMid("Entry Template"),
    boxLine(`[OBS] content: Concise source-grounded finding.`),
    boxLine(`      source:  { document: "doc_1", section: "section name" }`),
    boxLine(`      evidence:"short quote or locator"`),
    boxLine(`      conf:    ${confidenceDots(0.8)} 0.80`),
    boxLine(`      edges:   supports []  contradicts []  addresses []  opens []`),
    boxMid("Next Actions"),
    boxLine(`1. Register source text with bb_add_document.`),
    boxLine(`2. Add typed findings with bb_add_entries.`),
    boxLine(`3. Use relationships: ${EDGE_SYM.supports} support, ${EDGE_SYM.contradicts} contradict, ${EDGE_SYM.addresses} address.`),
    boxLine(`4. Check bb_convergence before final synthesis.`),
    boxBot(),
  ];
  return lines.join("\n");
}

export function renderList(blackboards: Array<Record<string, unknown>>): string {
  const lines = [
    boxTop("Blackboard MCP", "bb_list"),
    boxLine(`Project store: .blackboard/`),
    boxLine(`Found ${blackboards.length} blackboard${blackboards.length === 1 ? "" : "s"}`),
  ];

  if (blackboards.length === 0) {
    lines.push(boxMid("No Blackboards"));
    lines.push(boxLine("Call bb_create to start a new analysis."));
  } else {
    for (const b of blackboards) {
      lines.push(boxMid(String(b.blackboard_id)));
      const task = truncate(String(b.task || ""), BOX_W - 10);
      lines.push(boxLine(task));
      const entryTypes = b.entry_types as Record<string, number> || {};
      const typeLine = ["OBS", "ANL", "CAL", "STR", "GAP"]
        .map(t => `[${t}] ${entryTypes[t.toLowerCase().replace("anl", "analysis").replace("obs", "observation").replace("cal", "calculation").replace("str", "strategy")] || 0}`)
        .join("  ");
      lines.push(boxLine(`Entries ${b.entries || 0}  Docs ${Array.isArray(b.documents) ? b.documents.length : 0}  Open signals ${b.open_signals || 0}  Iter ${b.iteration || 0}`));

      const openSigs = Number(b.open_signals || 0);
      const entryCount = Number(b.entries || 0);
      const health = entryCount === 0 ? "⚠ empty" : openSigs === 0 ? "✓ stable" : "⚠ blocked";
      const score = entryCount === 0 ? 0 : openSigs === 0 ? 100 : Math.max(0, 100 - openSigs * 15);
      lines.push(boxLine(`Health ${pctBar(score)} ${health}  updated ${timeAgo(String(b.updated_at || ""))}`));
    }
  }

  lines.push(boxBot());
  if (blackboards.length > 0) {
    lines.push(`Hint: continue with bb_get_state on a relevant blackboard.`);
  }
  return lines.join("\n");
}

function timeAgo(iso: string): string {
  if (!iso) return "unknown";
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60000) return "just now";
    if (ms < 3600000) return `${Math.floor(ms / 60000)}m ago`;
    if (ms < 86400000) return `${Math.floor(ms / 3600000)}h ago`;
    return `${Math.floor(ms / 86400000)}d ago`;
  } catch { return "unknown"; }
}

export function renderAddDocument(
  docId: string, name: string, textLength: number,
  sections: string[], allDocs: Document[], summary: BbSummary, bb: Blackboard,
): string {
  const lines = [
    boxTop("Blackboard MCP", "bb_add_document"),
    boxLine(`✓ Registered document ${docId}`),
    boxLine(`Name: ${truncate(name, BOX_W - 12)}`),
    boxLine(`Size: ${textLength.toLocaleString()} chars`),
    boxMid("Sections"),
  ];
  if (sections.length === 0) {
    lines.push(boxLine("(no sections detected)"));
  } else {
    for (let i = 0; i < sections.length; i++) {
      lines.push(boxLine(`${String(i + 1).padStart(2, "0")}  ${truncate(sections[i], BOX_W - 10)}`));
    }
  }
  lines.push(boxMid("Document Coverage"));
  for (const d of allDocs) {
    const icon = d.read_status === "fully_read" ? "✓" : d.read_status === "partially_read" ? "⚠" : "⚠";
    const bar = progressBar(d.sections_read.length, d.sections.length || 1);
    const secStr = d.sections.length > 0 ? `${d.sections_read.length}/${d.sections.length} sections` : d.read_status;
    lines.push(boxLine(`${d.id} ${icon} ${truncate(d.name, 30)}  ${bar} ${secStr}`));
  }
  lines.push(...miniConvergence(bb).split("\n"));
  lines.push(boxBot());
  return lines.join("\n");
}

export function renderAddEntries(
  newEntries: Entry[], newSignals: Signal[],
  bb: Blackboard,
): string {
  const disputedByNew = bb.entries.filter(e => e.status === "disputed" && newEntries.some(ne =>
    ne.contradicts_entries.includes(e.id)));
  const supersededByNew = bb.entries.filter(e => e.status === "superseded" && newEntries.some(ne =>
    ne.supersedes_entries.includes(e.id)));

  const lines = [
    boxTop("Blackboard MCP", "bb_add_entries"),
    boxLine(`✓ Added ${newEntries.length} entr${newEntries.length === 1 ? "y" : "ies"} by worker: ${newEntries[0]?.created_by || "agent"}`),
  ];

  const effects: string[] = [];
  if (newSignals.length) effects.push(`${newSignals.length} signals opened`);
  if (disputedByNew.length) effects.push(`${disputedByNew.length} entry disputed`);
  if (supersededByNew.length) effects.push(`${supersededByNew.length} entry superseded`);
  const addressedSigs = newEntries.flatMap(e => e.addresses_signals);
  if (addressedSigs.length) effects.push(`${addressedSigs.length} signal${addressedSigs.length > 1 ? "s" : ""} addressed`);
  if (effects.length) lines.push(boxLine(`Auto-effects: ${effects.join(", ")}`));

  lines.push(boxMid("New Entries"));
  for (const e of newEntries) {
    lines.push(boxLine(`${e.id} ${badge(e.type)} ${confidenceDots(e.confidence)} ${sIcon(e.status)} ${truncate(e.content, BOX_W - 28)}`));
    if (e.source) {
      const src = e.source.section
        ? `${e.source.document} › ${e.source.section}`
        : e.source.document;
      lines.push(boxLine(`     src: ${truncate(src, BOX_W - 14)}`));
      if (e.source.evidence) {
        lines.push(boxLine(`     evidence: "${truncate(e.source.evidence, BOX_W - 20)}"`));
      }
    }
    const edges = edgeStr(e);
    if (edges) lines.push(boxLine(`     edges: ${edges}`));
    if (e.tags.length) lines.push(boxLine(`     tags: ${e.tags.join(", ")}`));
    lines.push(boxLine(""));
  }

  if (newSignals.length) {
    lines.push(boxMid("Signal Changes"));
    for (const s of newSignals) {
      lines.push(boxLine(`⚠ ${s.id} ${s.type} opened from ${s.origin_entry} priority=${s.priority}`));
      lines.push(boxLine(`  ${truncate(s.content, BOX_W - 6)}`));
    }
    for (const sId of addressedSigs) {
      const sig = bb.signals.find(s => s.id === sId);
      if (sig) {
        lines.push(boxLine(`✓ ${sig.id} ${sig.type} addressed by ${sig.addressed_by}`));
      }
    }
  }

  if (disputedByNew.length || supersededByNew.length) {
    lines.push(boxMid("Status Changes"));
    for (const e of disputedByNew) {
      const byWho = newEntries.filter(ne => ne.contradicts_entries.includes(e.id)).map(ne => ne.id).join(",");
      lines.push(boxLine(`✕ ${e.id} disputed by ${byWho}  confidence → ${e.confidence.toFixed(2)}  ${confidenceDots(e.confidence)}`));
    }
    for (const e of supersededByNew) {
      const byWho = newEntries.filter(ne => ne.supersedes_entries.includes(e.id)).map(ne => ne.id).join(",");
      lines.push(boxLine(`↻ ${e.id} superseded by ${byWho}`));
    }
  }

  lines.push(...miniConvergence(bb).split("\n"));
  lines.push(boxBot());
  return lines.join("\n");
}

export function renderAddSignal(signal: Signal | null, deduped: boolean, bb: Blackboard): string {
  const lines = [boxTop("Blackboard MCP", "bb_add_signal")];

  if (deduped) {
    lines.push(boxLine("✓ Signal deduped"));
    const existing = bb.signals.find(s => s.status === "open" && signal && s.content.toLowerCase() === signal.content.toLowerCase());
    if (existing) {
      lines.push(boxLine(`Existing open signal: ${existing.id} ${existing.priority} ${existing.type}`));
      lines.push(boxLine(truncate(existing.content, BOX_W - 4)));
    }
  } else if (signal) {
    lines.push(boxLine(`⚠ Opened signal ${signal.id}`));
    lines.push(boxLine(`Type: ${signal.type}        Priority: ${signal.priority}        Origin: ${signal.origin_entry || "manual"}`));
    lines.push(boxMid("Signal"));
    lines.push(boxLine(`${signal.id} ${sigIcon(signal.status)} ${signal.priority} ${signal.type}`));
    lines.push(boxLine(truncate(signal.content, BOX_W - 4)));
    lines.push(boxLine(`Created in iteration ${signal.iteration_created}`));
  }

  lines.push(...miniConvergence(bb).split("\n"));
  lines.push(boxBot());
  return lines.join("\n");
}

export function renderGetState(bb: Blackboard, entries: Entry[], signals: Signal[]): string {
  const summary = bbSummaryInternal(bb);
  const { score, status } = convergenceScore(bb);
  const totalSections = bb.documents.reduce((s, d) => s + d.sections.length, 0);
  const readSections = bb.documents.reduce((s, d) => s + d.sections_read.length, 0);

  const lines = [
    boxTop("Blackboard MCP", "bb_get_state"),
    boxLine(`${bb.id}  ${truncate(bb.task, BOX_W - 16)}`),
    boxLine(`Iteration ${bb.iteration}   Updated ${bb.updated_at}`),
    boxMid("Executive State"),
    boxLine(`Entries ${bb.entries.length} total / ${summary.active_entries} active / ${bb.entries.filter(e => e.status === "disputed").length} disputed / ${bb.entries.filter(e => e.status === "superseded").length} superseded`),
  ];

  const typeLine = ["observation", "analysis", "calculation", "strategy", "gap"]
    .map(t => `${badge(t)} ${summary.entry_types[t] || 0}`)
    .join("  ");
  lines.push(boxLine(`Types   ${typeLine}`));
  lines.push(boxLine(`Docs    ${bb.documents.length} total   ${progressBar(readSections, totalSections || 1)} ${readSections}/${totalSections} sections read`));

  const openSigs = bb.signals.filter(s => s.status === "open");
  const addressedSigs = bb.signals.filter(s => s.status === "addressed");
  lines.push(boxLine(`Signals ${bb.signals.length} total   ✓ ${addressedSigs.length} addressed   ⚠ ${openSigs.length} open`));
  lines.push(boxLine(`Health  ${pctBar(score)} ${score}%  ${status}`));

  // Documents
  lines.push(boxMid("Documents"));
  for (const d of bb.documents) {
    const icon = d.read_status === "fully_read" ? "✓" : "⚠";
    const bar = progressBar(d.sections_read.length, d.sections.length || 1);
    const secStr = d.sections.length > 0 ? `${d.sections_read.length}/${d.sections.length}` : d.read_status;
    lines.push(boxLine(`${d.id} ${icon} ${truncate(d.name, 28)}  ${bar} ${secStr}  ${d.text.length.toLocaleString()} chars`));
  }

  // Entries
  lines.push(boxMid("Entries"));
  for (const e of entries) {
    lines.push(boxLine(`${e.id.padEnd(4)} ${badge(e.type)} ${confidenceDots(e.confidence)} ${sIcon(e.status)} ${truncate(e.content, BOX_W - 28)}`));
    const srcParts: string[] = [];
    if (e.source) srcParts.push(`src ${e.source.document}${e.source.section ? " › " + e.source.section : ""}`);
    const edges = edgeStr(e);
    if (edges) srcParts.push(`edges ${edges}`);
    if (srcParts.length) lines.push(boxLine(`     ${srcParts.join("       ")}`));
  }

  // Open Signals
  if (openSigs.length) {
    lines.push(boxMid("Open Signals"));
    for (const s of openSigs) {
      lines.push(boxLine(`${s.id} ⚠ ${s.priority} ${s.type} from ${s.origin_entry}  ${truncate(s.content, BOX_W - 40)}`));
    }
  }

  // Reasoning Graph
  lines.push(boxMid("Reasoning Graph"));
  lines.push(...renderAsciiGraph(bb, entries).split("\n"));

  lines.push(boxBot());
  return lines.join("\n");
}

function bbSummaryInternal(bb: Blackboard): BbSummary {
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

export function renderMarkRead(
  doc: Document, newSections: string[] | undefined, bb: Blackboard,
): string {
  const lines = [
    boxTop("Blackboard MCP", "bb_mark_read"),
    boxLine(`✓ Updated reading coverage for ${doc.id} ${truncate(doc.name, BOX_W - 40)}`),
  ];
  if (newSections) {
    lines.push(boxLine(`Marked: ${newSections.join(", ")}`));
  } else {
    lines.push(boxLine("Marked: entire document"));
  }

  lines.push(boxMid("Document Coverage"));
  const bar = progressBar(doc.sections_read.length, doc.sections.length || 1);
  const secStr = doc.sections.length > 0 ? `${doc.sections_read.length}/${doc.sections.length} sections` : doc.read_status;
  lines.push(boxLine(`${doc.read_status === "fully_read" ? "✓" : "⚠"} ${doc.read_status.replace(/_/g, " ")}  ${bar} ${secStr}`));

  lines.push(...miniConvergence(bb).split("\n"));
  lines.push(boxBot());
  return lines.join("\n");
}

export function renderSearch(
  query: string,
  docResults: Array<{ doc_id: string; name: string; start: number; end: number; snippet: string }>,
  entryResults: Array<{ entry_id: string; type: string; content: string; confidence: number; status: string }>,
): string {
  const total = docResults.length + entryResults.length;
  const lines = [
    boxTop("Blackboard MCP", "bb_search"),
    boxLine(`Query: "${truncate(query, BOX_W - 14)}"`),
    boxLine(`Scope: documents + entries     Results: ${total} total (${docResults.length} docs, ${entryResults.length} entries)`),
    boxMid("Top Matches"),
  ];

  let rank = 1;
  const highlightPattern = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");

  for (const r of docResults.slice(0, 10)) {
    const snippet = r.snippet.replace(highlightPattern, "⟦$1⟧");
    lines.push(boxLine(`${rank}. ${r.doc_id} ${truncate(r.name, 40)}  chars ${r.start}-${r.end}`));
    const snippetLines = snippet.split("\n").slice(0, 3);
    for (const sl of snippetLines) {
      lines.push(boxLine(`   ${truncate(sl.trim(), BOX_W - 8)}`));
    }
    lines.push(boxLine(""));
    rank++;
  }

  for (const r of entryResults.slice(0, 10)) {
    const content = r.content.replace(highlightPattern, "⟦$1⟧");
    lines.push(boxLine(`${rank}. ${r.entry_id} ${badge(r.type)} ${confidenceDots(r.confidence)} ${sIcon(r.status)}`));
    lines.push(boxLine(`   ${truncate(content, BOX_W - 8)}`));
    lines.push(boxLine(""));
    rank++;
  }

  lines.push(boxBot());
  return lines.join("\n");
}

export function renderConvergence(bb: Blackboard): string {
  const { score, status, blockers } = convergenceScore(bb);
  const openSigs = bb.signals.filter(s => s.status === "open");
  const critical = openSigs.filter(s => s.priority === "critical");
  const high = openSigs.filter(s => s.priority === "high");
  const disputed = bb.entries.filter(e => e.status === "disputed");
  const unread = bb.documents.filter(d => d.read_status === "unread");
  const partial = bb.documents.filter(d => d.read_status === "partially_read");
  const converged = blockers.length === 0;

  const lines = [
    boxTop("Blackboard MCP", "bb_convergence"),
    boxLine(`${converged ? "✓ Converged" : "⚠ Not converged"}`),
    boxLine(`Overall ${pctBar(score)} ${score}%`),
  ];

  if (!converged) {
    lines.push(boxMid("Blockers"));
    let bNum = 1;
    for (const s of critical) {
      lines.push(boxLine(`${bNum}. ⚠ critical signal ${s.id} unresolved`));
      lines.push(boxLine(`   ${truncate(s.content, BOX_W - 8)}`));
      lines.push(boxLine(`   origin: ${s.origin_entry} ${badge(bb.entries.find(e => e.id === s.origin_entry)?.type || "gap")}`));
      bNum++;
    }
    for (const e of disputed) {
      lines.push(boxLine(`${bNum}. ✕ disputed entry ${e.id} still present`));
      lines.push(boxLine(`   ${truncate(e.content, BOX_W - 8)}`));
      const contradictedBy = bb.entries.filter(x => x.contradicts_entries.includes(e.id)).map(x => x.id);
      if (contradictedBy.length) lines.push(boxLine(`   contradicted by: ${contradictedBy.join(", ")}`));
      bNum++;
    }
    for (const d of unread) {
      lines.push(boxLine(`${bNum}. ⚠ unread document ${d.id} ${truncate(d.name, BOX_W - 30)}`));
      bNum++;
    }

    if (high.length || partial.length) {
      lines.push(boxMid("Non-Blocking Warnings"));
      for (const s of high) {
        lines.push(boxLine(`⚠ ${s.id} high ${s.type}: ${truncate(s.content, BOX_W - 25)}`));
      }
      for (const d of partial) {
        lines.push(boxLine(`⚠ ${d.id} partially read: ${truncate(d.name, BOX_W - 28)}`));
      }
    }
  } else {
    lines.push(boxMid("Cleared Gates"));
    lines.push(boxLine("✓ critical signals 0 open"));
    lines.push(boxLine("✓ disputed entries 0 active"));
    lines.push(boxLine("✓ unread documents 0"));
    lines.push(boxLine("✓ partial documents 0"));
  }

  // Readiness by dimension
  const totalSections = bb.documents.reduce((s, d) => s + d.sections.length, 0);
  const readSections = bb.documents.reduce((s, d) => s + d.sections_read.length, 0);
  const docPct = totalSections > 0 ? Math.round((readSections / totalSections) * 100) : (bb.documents.length > 0 ? 100 : 0);
  const sigTotal = bb.signals.length || 1;
  const sigAddressed = bb.signals.filter(s => s.status !== "open").length;
  const sigPct = Math.round((sigAddressed / sigTotal) * 100);
  const dispPct = bb.entries.length > 0 ? Math.round(((bb.entries.length - disputed.length) / bb.entries.length) * 100) : 100;

  lines.push(boxMid("Readiness by Dimension"));
  lines.push(boxLine(`Documents      ${docPct === 100 ? "✓" : "⚠"} ${pctBar(docPct)} ${docPct}%`));
  lines.push(boxLine(`Signals        ${sigPct === 100 ? "✓" : "⚠"} ${pctBar(sigPct)} ${sigPct}%`));
  lines.push(boxLine(`Contradictions ${dispPct === 100 ? "✓" : "✕"} ${pctBar(dispPct)} ${dispPct}%`));

  if (!converged) {
    lines.push(boxMid("Next Actions"));
    for (const s of critical) {
      lines.push(boxLine(`• Add observation addressing ${s.id}.`));
    }
    for (const e of disputed) {
      lines.push(boxLine(`• Supersede or retract ${e.id}.`));
    }
    for (const d of unread) {
      lines.push(boxLine(`• Read and mark ${d.id} ${d.name}.`));
    }
  }

  lines.push(boxBot());
  return lines.join("\n");
}

export function renderSynthesis(bb: Blackboard): string {
  const active = bb.entries.filter(e => e.status === "active");
  const highConf = active.filter(e => e.confidence >= 0.6);
  const disputed = bb.entries.filter(e => e.status === "disputed");
  const openSigs = bb.signals.filter(s => s.status === "open");
  const addressedSigs = bb.signals.filter(s => s.status === "addressed");
  const { score, status } = convergenceScore(bb);

  const lines = [
    boxTop("Blackboard MCP", "bb_synthesis"),
    boxLine("Final Intelligence Brief"),
    boxLine(`Task: ${truncate(bb.task, BOX_W - 10)}`),
    boxLine(`Status: ${status}     Iteration: ${bb.iteration}     Evidence: ${bb.documents.length} docs`),
  ];

  lines.push(boxMid("Must-Include Evidence"));
  for (const e of highConf) {
    lines.push(boxLine(`${e.id.padEnd(4)} ${badge(e.type)} ${confidenceDots(e.confidence)} ${truncate(e.content, BOX_W - 24)}`));
  }

  if (disputed.length) {
    lines.push(boxMid("Disputed (Resolve Before Citing)"));
    for (const e of disputed) {
      lines.push(boxLine(`${e.id.padEnd(4)} ${badge(e.type)} ${confidenceDots(e.confidence)} ✕ ${truncate(e.content, BOX_W - 26)}`));
      const contradictedBy = bb.entries.filter(x => x.contradicts_entries.includes(e.id)).map(x => x.id);
      if (contradictedBy.length) lines.push(boxLine(`     contradicted by: ${contradictedBy.join(", ")}`));
    }
  }

  if (addressedSigs.length) {
    lines.push(boxMid("Contradiction Handling"));
    for (const s of addressedSigs) {
      lines.push(boxLine(`✓ ${s.id} ${s.type} addressed by ${s.addressed_by}: ${truncate(s.content, BOX_W - 40)}`));
    }
  }

  if (openSigs.length) {
    lines.push(boxMid("Open Signals"));
    for (const s of openSigs) {
      lines.push(boxLine(`⚠ ${s.id} ${s.priority} ${s.type}: ${truncate(s.content, BOX_W - 30)}`));
    }
  } else {
    lines.push(boxMid("Open Signals"));
    lines.push(boxLine("none"));
  }

  lines.push(boxMid("Document Coverage"));
  for (const d of bb.documents) {
    lines.push(boxLine(`${d.read_status === "fully_read" ? "✓" : "⚠"} ${truncate(d.name, 40)} — ${d.read_status.replace(/_/g, " ")}`));
  }

  lines.push(boxBot());
  return lines.join("\n");
}

export function renderIterate(bb: Blackboard, prevIteration: number, expired: Signal[]): string {
  const { score, status } = convergenceScore(bb);
  const lines = [
    boxTop("Blackboard MCP", "bb_iterate"),
    boxLine(`↻ Advanced blackboard ${bb.id}`),
    boxLine(`Iteration ${prevIteration} → ${bb.iteration}`),
  ];

  if (expired.length) {
    lines.push(boxMid("Signal Aging"));
    for (const s of expired) {
      lines.push(boxLine(`↻ ${s.id} expired  ${s.priority} ${s.type}`));
      lines.push(boxLine(`  ${truncate(s.content, BOX_W - 6)}`));
    }
  } else {
    lines.push(boxMid("Signal Aging"));
    lines.push(boxLine("No signals expired this iteration."));
  }

  lines.push(...miniConvergence(bb).split("\n"));
  lines.push(boxBot());
  return lines.join("\n");
}

export function renderSnapshot(path: string, bb: Blackboard): string {
  const { score, status } = convergenceScore(bb);
  const lines = [
    boxTop("Blackboard MCP", "bb_snapshot"),
    boxLine("✓ Snapshot written"),
    boxLine(`Path: ${path}`),
    boxMid("Captured State"),
    boxLine(`Iteration ${bb.iteration}`),
    boxLine(`Entries   ${bb.entries.length} total / ${bb.entries.filter(e => e.status === "active").length} active / ${bb.entries.filter(e => e.status === "disputed").length} disputed / ${bb.entries.filter(e => e.status === "superseded").length} superseded`),
    boxLine(`Documents ${bb.documents.length} total / ${bb.documents.filter(d => d.read_status === "fully_read").length} fully read`),
    boxLine(`Signals   ${bb.signals.length} total / ${bb.signals.filter(s => s.status === "addressed").length} addressed / ${bb.signals.filter(s => s.status === "expired").length} expired / ${bb.signals.filter(s => s.status === "open").length} open`),
    boxLine(`Health    ${pctBar(score)} ${score}% ${status}`),
    boxBot(),
  ];
  return lines.join("\n");
}

export function renderExportConfirmation(path: string, bb: Blackboard, nodeCount: number, edgeCount: number): string {
  const { score, status } = convergenceScore(bb);
  const lines = [
    boxTop("Blackboard MCP", "bb_export"),
    boxLine("✓ HTML export created"),
    boxLine(`Blackboard: ${bb.id}`),
    boxMid("Written File"),
    boxLine(`✓ ${path}`),
    boxMid("Export Contents"),
    boxLine(`Nodes   ${nodeCount} entries, ${bb.signals.length} signals`),
    boxLine(`Edges   ${edgeCount} relationships`),
    boxLine(`Docs    ${bb.documents.length} documents (evidence snippets only)`),
    boxLine(`Mode    self-contained, no server required`),
    boxMid("Final State"),
    boxLine(`Iteration ${bb.iteration}   Health ${pctBar(score)} ${score}% ${status}`),
    boxLine(`Entries ${bb.entries.length}    Signals open ${bb.signals.filter(s => s.status === "open").length}    Documents read ${bb.documents.filter(d => d.read_status === "fully_read").length}/${bb.documents.length}`),
    boxBot(),
  ];
  return lines.join("\n");
}

export function renderDiagramConfirmation(nodeCount: number, edgeCount: number): string {
  const lines = [
    boxTop("Blackboard MCP", "bb_diagram"),
    boxLine("✓ Mermaid diagram generated"),
    boxLine(`Scope: entries + signals + documents     Nodes: ${nodeCount}     Edges: ${edgeCount}`),
    boxLine("Copy everything between the fences into any Mermaid renderer."),
    boxBot(),
  ];
  return lines.join("\n");
}

// ── ASCII Reasoning Graph ─────────────────────────────────────────────

function renderAsciiGraph(bb: Blackboard, entries: Entry[]): string {
  if (entries.length === 0) return "│ (no entries to graph)";

  const maxNodes = 40;
  const shown = entries.slice(0, maxNodes);
  const shownIds = new Set(shown.map(e => e.id));
  const lines: string[] = [];

  // Group entries by source document
  const byDoc = new Map<string, Entry[]>();
  const noDoc: Entry[] = [];

  for (const e of shown) {
    if (e.source?.document) {
      const docId = e.source.document;
      if (!byDoc.has(docId)) byDoc.set(docId, []);
      byDoc.get(docId)!.push(e);
    } else {
      noDoc.push(e);
    }
  }

  // Render document-grouped entries with edges
  for (const [docId, docEntries] of byDoc) {
    const doc = bb.documents.find(d => d.id === docId);
    const docName = doc ? truncate(doc.name, 30) : docId;
    lines.push(`│ ${docId} ${docName}`);

    for (const e of docEntries) {
      const node = `[${e.id} ${badge(e.type).slice(1, -1)} ${confidenceDots(e.confidence)}]`;
      const label = truncate(e.content, BOX_W - node.length - 10);
      lines.push(`│   ├──src──▶ ${node} ${label}`);

      // Show outgoing edges
      for (const tId of e.supports_entries) {
        if (shownIds.has(tId)) {
          const t = shown.find(x => x.id === tId)!;
          lines.push(`│   │         └──⇢──▶ [${tId} ${badge(t.type).slice(1, -1)}]`);
        }
      }
      for (const tId of e.contradicts_entries) {
        if (shownIds.has(tId)) {
          lines.push(`│   │         └──⇄──▶ [${tId}] ✕`);
        }
      }
      for (const tId of e.supersedes_entries) {
        if (shownIds.has(tId)) {
          lines.push(`│   │         └──↻──▶ [${tId}]`);
        }
      }
      for (const q of e.opens_questions) {
        const sig = bb.signals.find(s => s.content.toLowerCase() === q.toLowerCase());
        if (sig) {
          lines.push(`│   │         └──?──▶ ${sig.id} ${sigIcon(sig.status)} ${sig.priority}`);
        }
      }
      for (const sId of e.addresses_signals) {
        const sig = bb.signals.find(s => s.id === sId);
        if (sig) {
          lines.push(`│   │         └──✓──▶ ${sig.id} ✓ addressed`);
        }
      }
    }
    lines.push("│");
  }

  // Render entries without document source
  if (noDoc.length) {
    lines.push("│ (no source document)");
    for (const e of noDoc) {
      const node = `[${e.id} ${badge(e.type).slice(1, -1)} ${confidenceDots(e.confidence)}]`;
      lines.push(`│   ${node} ${truncate(e.content, BOX_W - node.length - 8)}`);
    }
  }

  if (entries.length > maxNodes) {
    lines.push(`│ Graph truncated: ${maxNodes} of ${entries.length} nodes shown`);
  }

  return lines.join("\n");
}
