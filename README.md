# Moltbook Digest

[中文文档 (Chinese)](./README.zh-CN.md)

`moltbook-digest` collects evidence from Moltbook posts/comments and prepares analysis-ready outputs.  
It supports two interpretation paths:

- `agent`: no external LLM call; your agent interprets the corpus
- `litellm`: automatic interpretation via LiteLLM using your configured provider

## Overview

- Semantic retrieval from Moltbook (`/search`)
- Full post + comment expansion (`/posts/{id}`, `/posts/{id}/comments`)
- Analysis-safe corpus generation: `digest.md` + `evidence.json`
- Dual interpretation modes: `agent` / `litellm`
- Minimal LLM config with provider defaults embedded in code
- Fault-tolerant collection (skip bad post IDs, continue with warnings)

## Project Structure

- `moltbook-digest/scripts/moltbook_digest.py`: main script
- `moltbook-digest/config.example.yaml`: LLM config template
- `moltbook-digest/pyproject.toml`: uv project dependencies
- `moltbook-digest/uv.lock`: uv lockfile for reproducible environments
- `moltbook-digest/SKILL.md`: skill usage guide
- `moltbook-digest/OPENCLAW.md`: OpenClaw integration guide
- `moltbook-digest/openclaw.skills.example.json5`: sample `~/.openclaw/openclaw.json` skill entry
- `moltbook-digest/references/api.md`: API notes and guidance

## Installation

Run from repository root:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --project moltbook-digest
```

## OpenClaw Integration

This skill is OpenClaw-ready:

- `SKILL.md` includes `metadata.openclaw` and a stable `skillKey` (`moltbookDigest`)
- command examples use `{baseDir}` to work from any runtime working directory
- API keys must be filled manually by the user (never by OpenClaw/agent)

See detailed steps in:

- `moltbook-digest/OPENCLAW.md`
- `moltbook-digest/openclaw.skills.example.json5`

## Configuration

Copy the template:

```bash
cp moltbook-digest/config.example.yaml moltbook-digest/config.yaml
```

Default template behavior:

- `active_provider: agent`
- no external API key required

For external LLM usage, set `active_provider` to one of:

- `openai`
- `claude`
- `gemini`
- `siliconflow`
- `minimax`
- `volcengine`

Notes:

- `moltbook-digest/config.yaml` is ignored by `.gitignore`
- template only exposes high-frequency fields; defaults like `api_base` and `api_key_env` are built into code
- `analysis.prompt_template` is now stored in config and supports placeholders:
  `{analysis_question}`, `{analysis_language}`, `{report_structure}`, `{analysis_input}`
- The same `analysis.prompt_template` pipeline is used by both `agent` and `litellm` paths.
  The only difference is the executor (`agent` itself vs external LLM API).
- Placeholder values are now centrally managed in config under:
  `analysis.default_language`, `analysis.question_template`, `analysis.report_structure`
- `{analysis_input}` is generated automatically from collected evidence (`digest.md` + `evidence.json`)
- The default template explicitly requires output in the user's preferred language (`{analysis_language}`)
- The default template now also enforces:
  first summarize post commonality/uniqueness, then apply first-principles analysis, and explicitly state assumptions when user intent is ambiguous

### Agent Configuration Requirement (MUST)

When an agent helps configure this skill, it must:

1. Read `moltbook-digest/config.example.yaml` before proposing any config values.
2. Ask the user how they want to configure `active_provider` (`agent` or external provider).
3. Ask which model/provider should be used and whether to keep or customize `analysis.prompt_template`.
4. Ask whether to keep or customize `analysis.question_template` and `analysis.report_structure`.
5. Ask how credentials should be supplied (existing env vars vs writing keys in local `config.yaml`).
6. Confirm `analysis_language` preference before generating analysis output.

Do not assume provider, model, key handling, or language preference without asking.

## Quick Start

### 1) Collection only (no interpretation)

```bash
uv run --project moltbook-digest python moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory architecture" \
  --query "agent memory failures and tradeoffs" \
  --analysis-mode none \
  --llm-config /tmp/not-exists.yaml
```

`--analysis-mode none` always remains pure collection, regardless of provider settings.

### 2) Agent interpretation (recommended default)

With `active_provider: agent` in `config.yaml`:

```bash
uv run --project moltbook-digest python moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory governance" \
  --analysis-mode auto \
  --llm-config moltbook-digest/config.yaml
```

The script auto-runs agent interpretation and embeds an **Agent Task Card** in `digest.md`.

### 3) LiteLLM interpretation

Set an external provider (for example `openai`) and fill key(s):

```bash
uv run --project moltbook-digest python moltbook-digest/scripts/moltbook_digest.py \
  --query "long-running agent memory patterns" \
  --analysis-mode auto \
  --llm-config moltbook-digest/config.yaml
```

The script auto-runs LiteLLM interpretation and writes `analysis_report.md`.

Explicit override example:

```bash
uv run --project moltbook-digest python moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory" \
  --analysis-mode litellm \
  --active-provider claude \
  --litellm-model "anthropic/claude-3-7-sonnet-latest" \
  --llm-config moltbook-digest/config.yaml
```

## Output Files

Default run output directory:

- `output/moltbook-digest/<timestamp>-<slug>/`

Core files:

- `digest.md`: unified markdown corpus (run summary, diagnostics, full post bodies, sampled comments, task card in agent mode)
- `evidence.json`: structured raw evidence
- `analysis_report.md`: final LLM-generated report in LiteLLM mode

Optional legacy files (only when `--emit-legacy-analysis-files` is set):

- `analysis_input.md`
- `agent_handoff.md` (agent mode only)

Compatibility note: if existing pipelines still expect `brief.md`, run with `--digest-name brief.md`.

## Common Parameters

- `--query`: repeatable; use 2-5 natural-language queries
- `--max-posts`: cap expanded posts
- `--comment-limit`: cap comments fetched per post
- `--submolt`: submolt filter
- `--analysis-mode`: `none | agent | litellm | auto`
- `--analysis-question`: target question the report must answer
- `--analysis-language`: output language. If omitted, uses `analysis.default_language` from config
- `--llm-config`: LLM config file path
- `--active-provider`: override config provider
- `--digest-name`: rename unified markdown output (default `digest.md`)
- `--evidence-name`: rename JSON evidence output (default `evidence.json`)
- `--emit-legacy-analysis-files`: also generate `analysis_input.md` / `agent_handoff.md`

Full parameter list:

```bash
uv run --project moltbook-digest python moltbook-digest/scripts/moltbook_digest.py --help
```

## Analysis Mode Rules

- `analysis-mode=none` -> collection only (no interpretation)
- `analysis-mode=agent` -> prepare Agent Task Card inside `digest.md` (no external LLM call)
- `analysis-mode=litellm` -> run LiteLLM and write `analysis_report.md`
- `agent` and `litellm` share the same configured analysis template and report structure.
- `analysis-mode=auto` + `active_provider=agent` -> auto agent interpretation
- `analysis-mode=auto` + external provider -> auto LiteLLM interpretation
- `analysis-mode=auto` + no explicit provider -> fallback to resolved default provider (`agent`)

## Fault Tolerance

- Search/page failures are non-fatal by default: the run continues with remaining queries/pages.
- Single post expansion failures (for example `404` on `/posts/{id}`) are skipped instead of aborting the full run.
- Comment fetch failures fall back to empty comments for that post.
- All non-fatal issues are recorded in `evidence.json` under `diagnostics.warnings`.

## Security

- never commit real `config.yaml` or API keys
- configure provider keys only in trusted environments
- keep reports explicit about evidence vs inference
