# Orchestrator of Three Cycles — Documentation

**The big question:** Does having AI agents argue with each other produce better code fixes than a single AI working alone?

---

## What This Does (In Plain English)

Imagine three AI agents working together to fix a bug:

1. **The Coder** — proposes a fix
2. **The Adversary** — tries to break the fix (finds edge cases, security holes, logic errors)
3. **The Critic** — judges whether the fix actually works and addresses the attacks

They go back and forth until the Critic says "this is good" or "needs more work." A human approves the final fix before it's applied.

We run this loop against a **control group** (single AI, no debate) on real bugs from real projects. The goal: measurable evidence that the debate loop produces more robust fixes.

---

## Quick Start

### Prerequisites
- Python 3.12+
- `uv` (install: `pip install uv`)
- Git repository (any repo you want to fix)

### Installation
```bash
# Clone the repo
git clone https://github.com/anujsingh-cse/orchestrator-of-three-cycles.git
cd orchestrator-of-three-cycles

# Install dependencies
uv sync --all-extras

# Add your NVIDIA NIM API key (free at https://build.nvidia.com)
cp .env.example .env
# Edit .env and paste your key
```

### Run Tests (No API Key Needed)
```bash
uv run pytest -q
```

---

## How to Run the Tool

### Basic Command Structure
```bash
uv run orchestrator "your task description" [options]
```

### Options
| Flag | Description | Default |
|------|-------------|---------|
| `--repo PATH` | Path to repository | Current directory |
| `--nim` | Use NVIDIA NIM models (needs API key) | Off (uses fake models) |
| `--api-key KEY` | NIM API key | Reads from `.env` |
| `--tui` | Use Textual TUI interface | Plain text streaming |
| `--auto-approve` | Auto-approve human gate | Reject (human must approve) |

---

## Use Cases

### 1. Fix a Bug in a Local Git Repository
```bash
cd /path/to/your/repo
uv run orchestrator "fix the login timeout bug" --repo . --auto-approve
```

### 2. Fix a Bug in a GitHub Repository
```bash
# Clone first
git clone https://github.com/owner/repo.git
cd repo
uv run orchestrator "fix the memory leak in data processor" --repo . --auto-approve
```

### 3. Work on Any Codebase (Not Just Git)
```bash
# The tool needs a git repo. For non-git folders:
cd /path/to/code
git init
git add .
git commit -m "initial"
uv run orchestrator "add error handling to parser" --repo . --auto-approve
```

### 4. Using Real AI Models (NIM Free Tier)
```bash
# Get free API key at https://build.nvidia.com
uv run orchestrator "refactor the authentication module" --repo . --nim --auto-approve
```

### 5. Interactive TUI Mode
```bash
uv run orchestrator "fix the race condition" --repo . --tui --auto-approve
```

### 6. With Human Approval (Default)
```bash
# You'll be prompted to approve each patch before it's applied
uv run orchestrator "add input validation" --repo .
```

---

## What Happens When You Run It

```
You type:     uv run orchestrator "fix the bug" --repo . --auto-approve
                │
                ▼
         ┌─────────────┐
         │   Coder     │  Proposes a fix (patch)
         └──────┬──────┘
                │
                ▼
         ┌─────────────┐
         │  Adversary  │  Attacks the fix (finds flaws)
         └──────┬──────┘
                │
                ▼
         ┌─────────────┐
         │   Critic    │  Judges: fix or needs work?
         └──────┬──────┘
                │
       ┌────────┴────────┐
       ▼                 ▼
   "Needs work"      "Good!"
       │                 │
       ▼                 ▼
   Loop back          Human gate
   to Coder              │
                         ▼
                  ┌─────────────┐
                  │   Runner    │  Applies patch, runs tests
                  └─────────────┘
```

**Every step is recorded** in an audit log (SQLite) that can be replayed exactly.

---

## Understanding the Output

### Plain Text Mode (Default)
```
[coder] patch 247 chars
[adversary] 3 keys
[critic] 2 keys
[arbiter] verdict=pass escalated=[]
[HITL] Approve applying this patch?
[gate] decision=approve
[record_gate] 1 keys
[runner] patch 0 chars
[done] session finished
```

### Key Terms
| Term | Meaning |
|------|---------|
| `patch N chars` | Coder produced a diff of N characters |
| `verdict=pass` | Critic approved the fix |
| `verdict=escalate` | Critic wants human review |
| `HITL` | Human-in-the-loop gate |
| `runner` | Applied patch and ran tests |

---

## Configuration

### Environment Variables (`.env`)
```bash
# Required for --nim mode
NIM_API_KEY=nvapi-your-key-here

# Optional: custom NIM endpoint
NIM_BASE_URL=https://integrate.api.nvidia.com/v1

# Local Ollama (alternative to NIM)
OLLAMA_HOST=http://localhost:11434
OLLAMA_DRAFT_MODEL=qwen3:8b
```

### Model Roster (Auto-Configured)
| Role | Model | Purpose |
|------|-------|---------|
| Coder | `nemotron-3-super-120b` | Fast, good at coding |
| Adversary | `nemotron-3-ultra-550b` | Deep reasoning for attacks |
| Critic | `nemotron-3-ultra-550b` | Judges fixes |

---

## Advanced Usage

### Run Against a Specific Branch
```bash
cd /path/to/repo
git checkout feature-branch
uv run orchestrator "fix the bug" --repo . --auto-approve
```

### Custom Test Command
```bash
# The runner uses `pytest -q -x` by default. To customize:
# Edit src/orchestrator/runner/process.py or fork the repo
```

### Audit Log Location
```
your-repo/.orchestrator/audit.db
```
Query with:
```bash
uv run python -c "
import sqlite3
conn = sqlite3.connect('your-repo/.orchestrator/audit.db')
for row in conn.execute('SELECT node, ts, gate_decision FROM events'):
    print(row)
"
```

---

## Troubleshooting

### "Not a git repository"
```bash
# Initialize git first
git init
git add .
git commit -m "init"
```

### "NIM_API_KEY required"
```bash
# Option 1: Use fake models (no key needed)
uv run orchestrator "task" --repo . --auto-approve

# Option 2: Add key to .env
echo "NIM_API_KEY=your-key" > .env
```

### "Textual not installed" (TUI mode)
```bash
uv sync --extra textual
# Or use plain mode (default)
```

### Tests Fail After Patch
The runner applies the patch and runs tests. If tests fail, the loop continues (up to 5 coder rounds by default).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Entry Point                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│   Adapters     │ │    Graph       │ │    Audit       │
│  (LLM Seam)    │ │  (LangGraph)   │ │    Sink        │
│                │ │                │ │  (SQLite DAG)  │
│ - NIM          │ │ - Coder        │ │                │
│ - Ollama       │ │ - Adversary    │ │ - Write-ahead  │
│ - Fake         │ │ - Critic       │ │ - Integrity    │
│ - Pacing       │ │ - Arbiter      │ │ - Replay       │
└────────────────┘ │ - Gate (HITL)  │ └────────────────┘
                   │ - Runner       │
                   └────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │      Worktree          │
              │  (Isolated Git Clone)  │
              └────────────────────────┘
```

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes
4. Run tests: `uv run pytest -q`
5. Run lint: `uv run ruff check .`
6. Submit PR

---

## License

MIT — Open source. The failure zoo data follows the license of each source repository (see `NOTICE`).

---

## Links

- **GitHub**: https://github.com/anujsingh-cse/orchestrator-of-three-cycles
- **NVIDIA NIM**: https://build.nvidia.com
- **LangGraph**: https://langchain-ai.github.io/langgraph/