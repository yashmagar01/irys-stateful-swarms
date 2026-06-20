# Blackboard MCP

Persistent structured reasoning for AI agents. Zero API calls, zero cost.

Agents create blackboards to track findings, contradictions, and gaps during complex analysis. Blackboards persist in `.blackboard/` in your project — new sessions pick up where previous ones left off.

## Install

### Claude Code

Add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "blackboard": {
      "type": "stdio",
      "command": "node",
      "args": ["/path/to/blackboard-mcp/dist/index.js"]
    }
  }
}
```

Or add via CLI:

```bash
claude mcp add blackboard -- node /path/to/blackboard-mcp/dist/index.js
```

### Codex CLI

Add to `.codex/config.toml`:

```toml
[mcp_servers.blackboard]
command = "node"
args = ["/path/to/blackboard-mcp/dist/index.js"]
```

### Building from source

```bash
git clone https://github.com/iqidis/ant-irys
cd ant-irys/packages/blackboard-mcp
npm install
npm run build
```

Then point your config to `dist/index.js` in the cloned directory.

## What happens

Once installed, your agent has 12 tools for structured reasoning:

- **bb_create** / **bb_list** — create or find existing blackboards
- **bb_add_document** — register source text for provenance tracking
- **bb_add_entries** — record typed findings (observation, analysis, calculation, gap)
- **bb_add_signal** — flag questions and gaps
- **bb_convergence** — check if analysis is complete (blockers: critical signals, disputed entries, unread docs)
- **bb_synthesis** — get structured evidence for final answer
- **bb_search** / **bb_get_state** / **bb_mark_read** / **bb_iterate** / **bb_snapshot**

The agent decides when to use these. Complex multi-source analysis triggers blackboard reasoning automatically via MCP instructions. Simple questions skip it.

## The value

**Persistence.** A blackboard created today is readable tomorrow. A new session on the same project checks `bb_list` first, finds the existing analysis, and builds on it instead of starting from scratch. Knowledge accumulates.

**Provenance.** Every finding traces back to a source document, section, and evidence quote. No unsourced claims.

**Contradiction tracking.** When entries contradict each other, both become "disputed" and confidence decays. The agent must resolve the contradiction before synthesizing. No silent hand-waving.

**Convergence gating.** The agent checks whether all critical signals are resolved, all documents are read, and no contradictions remain before presenting a final answer.
