import type { Blackboard, Entry } from "../types.js";

function mermaidEsc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function confidenceDots(c: number): string {
  const filled = Math.round(c * 5);
  return "●".repeat(filled) + "○".repeat(5 - filled);
}

function truncLabel(s: string, max = 40): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

const TYPE_BADGE: Record<string, string> = {
  observation: "OBS", analysis: "ANL", calculation: "CAL",
  strategy: "STR", gap: "GAP",
};

export interface GraphCounts {
  nodes: number;
  edges: number;
}

export function renderMermaidDiagram(
  bb: Blackboard,
  opts: { maxEntries?: number; includeSignals?: boolean; direction?: "TD" | "LR"; entryStatus?: Set<string> } = {},
): { mermaid: string; counts: GraphCounts } {
  const maxEntries = opts.maxEntries || 100;
  const includeSignals = opts.includeSignals !== false;
  const direction = opts.direction || "TD";
  const statusFilter = opts.entryStatus || new Set(["active", "disputed"]);

  const entries = bb.entries.filter(e => statusFilter.has(e.status)).slice(0, maxEntries);
  const entryIds = new Set(entries.map(e => e.id));
  let edgeCount = 0;

  const lines: string[] = [`graph ${direction}`];

  // Document nodes
  for (const d of bb.documents) {
    lines.push(`  doc_${d.id.replace(/[^a-zA-Z0-9_]/g, "")}[/"${mermaidEsc(d.id)}<br/>${mermaidEsc(truncLabel(d.name, 30))}"/]`);
  }
  lines.push("");

  // Entry nodes
  for (const e of entries) {
    const badge = TYPE_BADGE[e.type] || e.type.slice(0, 3).toUpperCase();
    const content = mermaidEsc(truncLabel(e.content, 35));
    const dots = confidenceDots(e.confidence);
    const nodeId = e.id.replace(/[^a-zA-Z0-9_]/g, "");
    lines.push(`  ${nodeId}["${mermaidEsc(e.id)} [${badge}] ${dots}<br/>${content}"]`);
  }
  lines.push("");

  // Signal nodes
  const signalIds = new Set<string>();
  if (includeSignals) {
    for (const s of bb.signals) {
      const icon = s.status === "open" ? "⚠" : s.status === "addressed" ? "✓" : "↻";
      const nodeId = s.id.replace(/[^a-zA-Z0-9_]/g, "");
      lines.push(`  ${nodeId}(("${mermaidEsc(s.id)} ${icon}<br/>${mermaidEsc(truncLabel(s.content, 25))}"))`)
      signalIds.add(s.id);
    }
    lines.push("");
  }

  // Source edges (document -> entry)
  for (const e of entries) {
    if (e.source?.document) {
      const doc = bb.documents.find(d => d.id === e.source!.document || d.name === e.source!.document);
      if (doc) {
        const docNodeId = `doc_${doc.id.replace(/[^a-zA-Z0-9_]/g, "")}`;
        const entryNodeId = e.id.replace(/[^a-zA-Z0-9_]/g, "");
        lines.push(`  ${docNodeId} -->|source| ${entryNodeId}`);
        edgeCount++;
      }
    }
  }
  lines.push("");

  // Relationship edges
  for (const e of entries) {
    const fromId = e.id.replace(/[^a-zA-Z0-9_]/g, "");

    for (const tId of e.supports_entries) {
      if (entryIds.has(tId)) {
        lines.push(`  ${fromId} -->|supports| ${tId.replace(/[^a-zA-Z0-9_]/g, "")}`);
        edgeCount++;
      }
    }

    for (const tId of e.contradicts_entries) {
      if (entryIds.has(tId)) {
        lines.push(`  ${fromId} -. contradicts .-> ${tId.replace(/[^a-zA-Z0-9_]/g, "")}`);
        edgeCount++;
      }
    }

    for (const tId of e.supersedes_entries) {
      if (entryIds.has(tId)) {
        lines.push(`  ${fromId} == supersedes ==> ${tId.replace(/[^a-zA-Z0-9_]/g, "")}`);
        edgeCount++;
      }
    }

    if (includeSignals) {
      for (const sId of e.addresses_signals) {
        if (signalIds.has(sId)) {
          lines.push(`  ${fromId} -->|addresses| ${sId.replace(/[^a-zA-Z0-9_]/g, "")}`);
          edgeCount++;
        }
      }
    }
  }
  lines.push("");

  // Class definitions (dark theme)
  lines.push("  classDef observation fill:#123b5d,stroke:#67b7dc,color:#ffffff");
  lines.push("  classDef analysis fill:#3c2f6f,stroke:#b69cff,color:#ffffff");
  lines.push("  classDef calculation fill:#4b3b08,stroke:#ffd166,color:#ffffff");
  lines.push("  classDef strategy fill:#0f5132,stroke:#75dd9b,color:#ffffff");
  lines.push("  classDef gap fill:#5c1f1f,stroke:#ff8a8a,color:#ffffff");
  lines.push("  classDef disputed fill:#3a1a1a,stroke:#ff4d4d,color:#ffffff,stroke-width:3px");
  lines.push("  classDef signalOpen fill:#5c1f1f,stroke:#ff8a8a,color:#ffffff");
  lines.push("  classDef signalDone fill:#1f4d2e,stroke:#75dd9b,color:#ffffff");
  lines.push("  classDef signalExpired fill:#3a3a3a,stroke:#aaaaaa,color:#ffffff");
  lines.push("  classDef document fill:#1f2937,stroke:#9ca3af,color:#ffffff");
  lines.push("");

  // Apply classes
  const docNodes = bb.documents.map(d => `doc_${d.id.replace(/[^a-zA-Z0-9_]/g, "")}`);
  if (docNodes.length) lines.push(`  class ${docNodes.join(",")} document`);

  const byType: Record<string, string[]> = {};
  for (const e of entries) {
    const cls = e.status === "disputed" ? "disputed" : e.type;
    if (!byType[cls]) byType[cls] = [];
    byType[cls].push(e.id.replace(/[^a-zA-Z0-9_]/g, ""));
  }
  for (const [cls, ids] of Object.entries(byType)) {
    lines.push(`  class ${ids.join(",")} ${cls}`);
  }

  if (includeSignals) {
    const sigByStatus: Record<string, string[]> = {};
    for (const s of bb.signals) {
      const cls = s.status === "open" ? "signalOpen" : s.status === "addressed" ? "signalDone" : "signalExpired";
      if (!sigByStatus[cls]) sigByStatus[cls] = [];
      sigByStatus[cls].push(s.id.replace(/[^a-zA-Z0-9_]/g, ""));
    }
    for (const [cls, ids] of Object.entries(sigByStatus)) {
      lines.push(`  class ${ids.join(",")} ${cls}`);
    }
  }

  const nodeCount = entries.length + bb.documents.length + (includeSignals ? bb.signals.length : 0);
  return {
    mermaid: lines.join("\n"),
    counts: { nodes: nodeCount, edges: edgeCount },
  };
}
