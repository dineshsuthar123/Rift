# CI/CD Healing Agent — LangGraph Orchestrator (Member 2)

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Node.js Backend (Member 1)                 │
│  POST /api/analyze → clones repo → calls agent.py            │
└────────────────────────┬─────────────────────────────────────┘
                         │  subprocess / HTTP
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              LangGraph Agent (Member 2 — THIS)                │
│                                                               │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐      │
│  │ analyze_logs │──▶│ generate_fix  │──▶│  apply_fix   │      │
│  └──────┬──────┘   └──────────────┘   └──────┬───────┘      │
│         │                                      │              │
│         │         ┌──────────────┐              │              │
│         │         │  save_results │◀────────────│              │
│         │         └──────┬───────┘    ┌────────┴───────┐     │
│         │                │            │   verify_fix    │     │
│         │                ▼            └────────┬───────┘     │
│         │              END                     │              │
│         │                              (loop if iter < max)   │
│         └──────────────────────────────────────┘              │
└──────────────────────────┬───────────────────────────────────┘
                           │  Docker run
                           ▼
┌──────────────────────────────────────────────────────────────┐
│              Docker Sandbox (Member 3)                         │
│  run_tests.sh → ruff + pytest → errors.json                  │
└──────────────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | LangGraph state machine — main orchestrator |
| `config.py` | Environment variables and constants |
| `error_parser.py` | Reads and classifies errors from errors.json |
| `fix_generator.py` | LLM integration — generates structured fixes |
| `file_patcher.py` | Applies code fixes to source files |
| `sandbox_runner.py` | Runs Docker sandbox / local fallback |
| `api_server.py` | Flask REST API for Member 1 integration |
| `INTEGRATION.md` | Integration contracts for all 3 members |

## Setup

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

## Usage

### CLI (standalone)
```bash
python agent.py /path/to/repo "TEAM NAME" "Leader Name" 5
```

### API Server (for Member 1 integration)
```bash
python api_server.py
# Then POST to http://localhost:5000/api/run-agent
```

### Run Tests
```bash
cd agent
python -m pytest tests/ -v
```

## Output Format

Every fix produces the **exact** string format required:
```
LINTING error in src/utils.py line 15 -> Fix: remove the import statement
```

Valid bug types: `LINTING`, `SYNTAX`, `LOGIC`, `TYPE_ERROR`, `IMPORT`, `INDENTATION`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model |
| `MAX_ITERATIONS` | `5` | Max fix-verify loops |
| `DOCKER_IMAGE` | `cicd-sandbox:latest` | Docker image name |
| `DOCKER_TIMEOUT` | `120` | Sandbox timeout (seconds) |
| `AGENT_PORT` | `5000` | Flask API port |

## Supported Bug Types

| Type | Detection Source | Example |
|------|-----------------|---------|
| LINTING | Ruff | Unused variables, line too long |
| SYNTAX | Ruff / Pytest | Missing colons, brackets |
| LOGIC | Pytest | Off-by-one, wrong operators |
| TYPE_ERROR | Pytest | Incorrect argument types |
| IMPORT | Ruff / Pytest | Unused or missing imports |
| INDENTATION | Ruff / Pytest | Incorrect indentation |
