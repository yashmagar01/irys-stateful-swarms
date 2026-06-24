export interface EntrySource {
  document: string;
  section: string;
  evidence: string;
}

export interface Entry {
  id: string;
  type: "observation" | "analysis" | "calculation" | "strategy" | "gap";
  label: string;
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

export interface Signal {
  id: string;
  type: "question" | "convergence_gap" | "contradiction_resolution" | "source_gap";
  content: string;
  origin_entry: string;
  priority: "low" | "medium" | "high" | "critical";
  status: "open" | "addressed" | "expired";
  addressed_by: string | null;
  iteration_created: number;
}

export interface Document {
  id: string;
  name: string;
  text: string;
  sections: string[];
  sections_read: string[];
  read_status: "unread" | "partially_read" | "fully_read";
}

export interface Blackboard {
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

export interface BbSummary {
  iteration: number;
  total_entries: number;
  active_entries: number;
  entry_types: Record<string, number>;
  open_signals: number;
  critical_signals: number;
  documents: number;
  documents_unread: number;
}
