# Moltbook Digest

[中文文档 (Chinese)](./README.zh-CN.md)

`moltbook-digest` collects evidence from Moltbook posts/comments and prepares analysis-ready outputs.  
It supports two interpretation paths:

- `agent`: no external LLM call; your agent interprets the corpus
- `litellm`: automatic interpretation via LiteLLM using your configured provider

## Overview

- Semantic retrieval from Moltbook (`/search`)
- Full post + comment expansion (`/posts/{id}`, `/posts/{id}/comments`)
- Analysis-safe corpus generation: `brief.md` + `evidence.json`
- Dual interpretation modes: `agent` / `litellm`
- Minimal LLM config with provider defaults embedded in code

## Project Structure

- `moltbook-digest/scripts/moltbook_digest.py`: main script
- `moltbook-digest/config.example.yaml`: LLM config template
- `moltbook-digest/requirements.txt`: dependencies
- `moltbook-digest/SKILL.md`: skill usage guide
- `moltbook-digest/references/api.md`: API notes and guidance

## Installation

Run from repository root:

```bash
pip install -r moltbook-digest/requirements.txt
```

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
- The default template explicitly requires output in the user's preferred language (`{analysis_language}`)

### Agent Configuration Requirement (MUST)

When an agent helps configure this skill, it must:

1. Read `moltbook-digest/config.example.yaml` before proposing any config values.
2. Ask the user how they want to configure `active_provider` (`agent` or external provider).
3. Ask which model/provider should be used and whether to keep or customize `analysis.prompt_template`.
4. Ask how credentials should be supplied (existing env vars vs writing keys in local `config.yaml`).
5. Confirm `analysis_language` preference before generating analysis output.

Do not assume provider, model, key handling, or language preference without asking.

## Quick Start

### 1) Collection only (no interpretation)

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory architecture" \
  --query "agent memory failures and tradeoffs" \
  --analysis-mode none \
  --llm-config /tmp/not-exists.yaml
```

Without a valid config, `--analysis-mode none` remains pure collection.

### 2) Agent interpretation (recommended default)

With `active_provider: agent` in `config.yaml`:

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory governance" \
  --analysis-mode none \
  --llm-config moltbook-digest/config.yaml
```

The script auto-runs agent interpretation and writes `analysis_input.md` + `agent_handoff.md`.

### 3) LiteLLM interpretation

Set an external provider (for example `openai`) and fill key(s):

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "long-running agent memory patterns" \
  --analysis-mode none \
  --llm-config moltbook-digest/config.yaml
```

The script auto-runs LiteLLM interpretation and writes `analysis_report.md`.

Explicit override example:

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
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

- `brief.md`: full post bodies + representative comments (analysis corpus)
- `evidence.json`: structured raw evidence
- `analysis_input.md`: normalized interpretation input (when analysis is enabled)
- `agent_handoff.md`: interpretation task brief for agent mode
- `analysis_report.md`: final LLM-generated report in LiteLLM mode

## Common Parameters

- `--query`: repeatable; use 2-5 natural-language queries
- `--max-posts`: cap expanded posts
- `--comment-limit`: cap comments fetched per post
- `--submolt`: submolt filter
- `--analysis-mode`: `none | agent | litellm | both`
- `--analysis-question`: target question the report must answer
- `--analysis-language`: output language, default `zh-CN`
- `--llm-config`: LLM config file path
- `--active-provider`: override config provider

Full parameter list:

```bash
python3 moltbook-digest/scripts/moltbook_digest.py --help
```

## Auto Mode Rules

- `analysis-mode=none` + `active_provider=agent` -> auto agent interpretation
- `analysis-mode=none` + external provider -> auto LiteLLM interpretation
- `analysis-mode=none` + no valid provider config -> collection only

## Security

- never commit real `config.yaml` or API keys
- configure provider keys only in trusted environments
- keep reports explicit about evidence vs inference
