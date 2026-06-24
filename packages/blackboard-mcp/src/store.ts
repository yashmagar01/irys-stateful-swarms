import { mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync, renameSync } from "fs";
import { join } from "path";
import type { Blackboard } from "./types.js";

const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd();
export const STORE_ROOT = join(PROJECT_DIR, ".blackboard");
export const blackboards = new Map<string, Blackboard>();

let entryCounter = 0;
let signalCounter = 0;

export function genEntryId(): string {
  return `e${++entryCounter}`;
}

export function genSignalId(): string {
  return `s${++signalCounter}`;
}

export function stateDir(bbId: string): string {
  const d = join(STORE_ROOT, bbId);
  mkdirSync(d, { recursive: true });
  return d;
}

export function saveState(bb: Blackboard): void {
  const d = stateDir(bb.id);
  bb.updated_at = new Date().toISOString();
  const tmp = join(d, "state.tmp");
  const target = join(d, "state.json");
  writeFileSync(tmp, JSON.stringify(bb, null, 2), "utf-8");
  renameSync(tmp, target);
}

export function loadState(bbId: string): Blackboard | null {
  if (blackboards.has(bbId)) return blackboards.get(bbId)!;
  const f = join(stateDir(bbId), "state.json");
  if (!existsSync(f)) return null;
  const bb: Blackboard = JSON.parse(readFileSync(f, "utf-8"));
  blackboards.set(bbId, bb);
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

export function listAllBlackboards(): Array<Record<string, unknown>> {
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
  return results;
}
