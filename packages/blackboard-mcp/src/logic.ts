import type { Blackboard, Entry, Signal, EntrySource, BbSummary } from "./types.js";
import { genEntryId, genSignalId } from "./store.js";

export function bbSummary(bb: Blackboard): BbSummary {
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

export function addEntriesToBb(bb: Blackboard, rawEntries: unknown[], workerId: string): { newEntries: Entry[]; newSignals: Signal[] } {
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
      label: String(ed.label || ""),
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

    for (const q of entry.opens_questions) {
      if (!q.trim()) continue;
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

    for (const cId of entry.contradicts_entries) {
      const target = bb.entries.find(e => e.id === cId);
      if (target && target.status === "active") {
        target.status = "disputed";
        target.confidence = Math.max(0, target.confidence - 0.15);
      }
    }

    for (const sId of entry.addresses_signals) {
      const sig = bb.signals.find(s => s.id === sId);
      if (sig && sig.status === "open") {
        sig.status = "addressed";
        sig.addressed_by = entry.id;
      }
    }

    for (const sId of entry.supersedes_entries) {
      const target = bb.entries.find(e => e.id === sId);
      if (target) target.status = "superseded";
    }

    for (const sId of entry.supports_entries) {
      const target = bb.entries.find(e => e.id === sId);
      if (target && target.status === "active") {
        target.confidence = Math.min(1, target.confidence + 0.05);
      }
    }
  }

  return { newEntries, newSignals };
}

export function entryDict(e: Entry): Record<string, unknown> {
  const d: Record<string, unknown> = {
    id: e.id, type: e.type, content: e.content,
    confidence: e.confidence, status: e.status,
    tags: e.tags, created_by: e.created_by, iteration: e.iteration,
  };
  if (e.label) d.label = e.label;
  if (e.source) d.source = e.source;
  if (e.opens_questions.length) d.opens_questions = e.opens_questions;
  if (e.supports_entries.length) d.supports_entries = e.supports_entries;
  if (e.contradicts_entries.length) d.contradicts_entries = e.contradicts_entries;
  if (e.supersedes_entries.length) d.supersedes_entries = e.supersedes_entries;
  if (e.addresses_signals.length) d.addresses_signals = e.addresses_signals;
  return d;
}

export function signalDict(s: Signal) {
  return {
    id: s.id, type: s.type, content: s.content,
    origin_entry: s.origin_entry, priority: s.priority,
    status: s.status, addressed_by: s.addressed_by,
    iteration_created: s.iteration_created,
  };
}

export function docDict(d: { id: string; name: string; text: string; sections: string[]; sections_read: string[]; read_status: string }) {
  return {
    id: d.id, name: d.name, sections: d.sections,
    sections_read: d.sections_read, read_status: d.read_status,
    text_length: d.text.length,
  };
}
