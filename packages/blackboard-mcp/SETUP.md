# Blackboard MCP

Persistent structured reasoning for AI agents. Zero API calls, zero cost.

Agents create blackboards to track findings, contradictions, and gaps during complex analysis. Blackboards persist in `.blackboard/` in your project — new sessions pick up where previous ones left off.

## Quick Start

### 1. Build

```bash
git clone https://github.com/iqidis/ant-irys
cd ant-irys/packages/blackboard-mcp
npm install
npm run build
```

### 2. Configure your AI agent

**Claude Code** — add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "blackboard": {
      "type": "stdio",
      "command": "node",
      "args": ["/absolute/path/to/ant-irys/packages/blackboard-mcp/dist/index.js"]
    }
  }
}
```

Or add via CLI:

```bash
claude mcp add blackboard -- node /absolute/path/to/ant-irys/packages/blackboard-mcp/dist/index.js
```

**Codex CLI** — add to `.codex/config.toml`:

```toml
[mcp_servers.blackboard]
command = "node"
args = ["/absolute/path/to/ant-irys/packages/blackboard-mcp/dist/index.js"]
```

Replace `/absolute/path/to/` with the real path on your machine.

### 3. Verify

Start your agent and ask it to analyze something complex. It should automatically use the blackboard tools. You can confirm by asking:

> "List all blackboards"

This calls `bb_list` — if it returns results, you're connected.

### 4. Run tests

```bash
npm test
```

Expects 33/33 passing.

## Tools (14)

| Tool | Purpose |
|------|---------|
| `bb_create` | Create a new blackboard for a task |
| `bb_list` | List all blackboards in this project |
| `bb_add_document` | Register source text for provenance tracking |
| `bb_add_entries` | Record typed findings (observation, analysis, calculation, strategy, gap) |
| `bb_add_signal` | Flag questions, gaps, and concerns |
| `bb_get_state` | Get full blackboard state |
| `bb_mark_read` | Mark document sections as read |
| `bb_search` | Search entries by content |
| `bb_convergence` | Check if analysis is complete (blockers: critical signals, disputed entries, unread docs) |
| `bb_synthesis` | Get structured evidence packet for final answer |
| `bb_iterate` | Advance to next iteration |
| `bb_snapshot` | Save a point-in-time snapshot |
| `bb_diagram` | Generate a Mermaid diagram of the reasoning graph |
| `bb_export` | Export an interactive HTML visualization of the full blackboard |

## HTML Export

`bb_export` generates a self-contained HTML file (~80KB) with:

- **Force-directed graph** — canvas-based, handles 500+ nodes. Color-coded by type, sized by confidence.
- **Replay slider** — step through iterations to watch analysis evolve. Auto-play mode with narration.
- **Insight cards** — most central finding, top conclusion, key conflict, main blocker.
- **Convergence tracking** — readiness score, coverage pressure sparkline, trajectory over iterations.
- **Full detail panel** — click any node to see content, source evidence, connections, influence score.
- **Findings index** — sortable list of all entries by influence, confidence, or iteration.
- **Contradiction map** — disputed findings shown side-by-side.
- **Source coverage** — which documents are read, partially read, or unread.
- **Keyboard shortcuts** — Tab (cycle nodes), F (fit), L (list view), Space (play), Esc (deselect).

The HTML is fully self-contained — no CDN, no external dependencies. Open it in any browser, share it with anyone.

Exports are saved to `.blackboard/<id>/exports/`.

## How it works

The agent decides when to use the blackboard. Complex multi-source analysis triggers it automatically via MCP instructions. Simple questions skip it.

**Persistence.** A blackboard created today is readable tomorrow. New sessions call `bb_list` first, find existing analysis, and build on it.

**Provenance.** Every finding traces to a source document, section, and evidence quote.

**Contradiction tracking.** When entries contradict each other, both become "disputed" and confidence decays. The agent must resolve contradictions before synthesizing.

**Convergence gating.** `bb_convergence` checks whether all critical signals are resolved, all documents are read, and no contradictions remain.

## Storage

Blackboards are stored as JSON in `.blackboard/<id>/state.json` in your project directory. Add `.blackboard/` to `.gitignore` if you don't want to commit analysis state.

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `BLACKBOARD_MCP_OUTPUT` | `rich` | Set to `json` for machine-readable tool responses |
