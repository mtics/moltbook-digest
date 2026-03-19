---
name: moltbook-digest
description: Research Moltbook posts and comment threads using Moltbook's public API. Use when the user wants keyword-based discovery, semantic search, deep summarization, trend analysis, or a structured report about Moltbook discussions.
---

# Moltbook Digest

Use this skill when the user wants more than a scrape of Moltbook posts. The goal is to turn keyword-driven discovery into a defensible report with evidence, tradeoffs, and gaps.

## Shortest Path

For read-only research, prefer Moltbook's public API over browser scraping:

1. Use semantic search to gather candidate posts and comments.
2. Expand the strongest matches into full post and comment context.
3. Synthesize a report that explains themes, disagreements, blind spots, and next steps.

Do not default to webpage scraping unless the API stops returning the needed content.

## Before You Run

Do a quick framing pass before collecting data.

- If the user's goal is vague, ask one focused question about the decision they are trying to make.
- If the user only gives a loose keyword, convert it into 2-5 natural-language search queries and say so.
- If the user asks for "everything" on a large topic, narrow by time window, submolt, or decision context before claiming coverage.

Good prompts to recover intent:

- What question should the report answer?
- Is this exploratory scanning or decision support?
- Should we optimize for breadth, depth, or recent activity?

If the user does not answer, make a reasonable assumption and state it in the report.

## Read vs Write

Default to read-only research.

- Read-only research can use public endpoints such as `/search`, `/posts`, `/posts/{id}`, and `/posts/{id}/comments`.
- Personalized feeds, notifications, and any write action require auth and should only happen after the user explicitly asks.
- If auth is needed, use `https://www.moltbook.com` with `www`. Do not send Moltbook credentials anywhere else.

## Recommended Workflow

### 1. Frame the research brief

Capture four things:

- The user question
- The working queries
- The scope constraints
- What a useful report should help the user decide

### 2. Gather evidence

Use `scripts/moltbook_digest.py` to collect a normalized evidence pack.

Typical command:

```bash
uv run --project . python scripts/moltbook_digest.py \
  --query "how agents handle memory" \
  --query "persistent memory architectures for agents" \
  --max-posts 6 \
  --comment-limit 12
```

The script writes:

- `output/moltbook-digest/<timestamp>-<slug>/digest.md`
- `output/moltbook-digest/<timestamp>-<slug>/evidence.json`

`digest.md` should remain analysis-safe: keep full expanded post bodies and sampled comments by default. Use it as the markdown corpus for downstream reasoning, not as a teaser summary.

Use repeated `--query` flags when the user's wording is too narrow for reliable coverage.

### 2.5 Choose an interpretation path

After evidence collection, choose one of two analysis methods:

1. LiteLLM path (standalone LLM endpoint)
2. Agent path (your current agent reads and interprets the corpus)

Both methods start from the same evidence pack.

### 3. Expand only the posts that matter

Do not analyze every hit equally.

- Prefer posts that match multiple queries.
- Prefer posts with strong semantic relevance or repeated recurrence across the corpus.
- Pull comments for context, especially when the user wants controversy, consensus, objections, or practical takeaways.
- If the user names a submolt, pass `--submolt NAME` to keep the corpus honest.

### 4. Analyze, do not just summarize

Your report should answer questions like:

- What are the main themes?
- Where do authors agree?
- Where do they disagree or use different assumptions?
- Which claims are anecdotal versus broadly echoed?
- What is missing from the conversation?
- What follow-up queries would improve confidence?

Never imply exhaustive coverage unless you truly scanned the full relevant corpus.

## Interpretation Modes

### Mode A: LiteLLM (standalone LLM interface)

Use this when the user wants script-level automatic interpretation.

Install:

```bash
uv sync --project .
```

Provider config template:

```bash
cp config.example.yaml config.yaml
```

Then fill real keys in `config.yaml` (do not commit it). The repository tracks only `config.example.yaml`.
Prompt template for LiteLLM analysis also lives in `config.yaml` under `analysis.prompt_template`.
It should require output in `{analysis_language}`, force a "summary first" pass (commonality + uniqueness), then apply first-principles reasoning and explicit assumptions.
Placeholder values are also managed in config:
- `analysis.default_language` for `{analysis_language}` default
- `analysis.question_template` for `{analysis_question}` generation
- `analysis.report_structure` for `{report_structure}`
`{analysis_input}` is generated automatically from collected evidence.
If `active_provider` is set to `agent`, no external key is required and interpretation runs in agent mode.
The config file is intentionally minimal. Most provider defaults (such as base URL and key env names) are built into `scripts/moltbook_digest.py`.

Auto-selection behavior:

- `--analysis-mode none` always keeps collection-only behavior.
- If `--analysis-mode auto` and `active_provider=agent`, the script runs agent interpretation mode.
- If `--analysis-mode auto` and `active_provider` is an external provider, the script runs LiteLLM mode.
- If `--analysis-mode auto` and provider is not explicitly configured, the script falls back to the resolved default provider (`agent`).

Example:

```bash
uv run --project . python scripts/moltbook_digest.py \
  --query "how agents handle memory" \
  --query "persistent memory architectures for agents" \
  --analysis-mode litellm \
  --litellm-model "openai/gpt-4.1-mini" \
  --analysis-question "What durable design patterns and failure modes emerge from these discussions?"
```

Outputs added to the run folder:

- `analysis_report.md` (LLM-generated deep analysis)

Optional legacy output (for backward compatibility only):

- `analysis_input.md` (requires `--emit-legacy-analysis-files`)

Important:

- Keep `--analysis-post-char-limit` and `--analysis-context-char-limit` high enough for deep analysis.
- If the LLM call fails because context is too large, reduce `--max-posts` or `--comment-limit` first, then tune the analysis limits.

### Mode B: Agent (direct interpretation by your agent)

Use this when the user wants their own agent to perform interpretation, without wiring an external LLM API in the script.

Example:

```bash
uv run --project . python scripts/moltbook_digest.py \
  --query "agent memory governance" \
  --analysis-mode agent \
  --analysis-question "What are the core governance disagreements and practical policy implications?"
```

Outputs added to the run folder:

- Agent task requirements embedded directly in `digest.md`

Optional legacy outputs (for backward compatibility only):

- `analysis_input.md`
- `agent_handoff.md`

### 5. Deliver a complete report

Unless the user asks for a different format, structure the report like this:

1. Corpus summary: per-post key points + commonality/uniqueness
2. First-principles framing: user goal, constraints, tradeoffs, assumptions
3. Deep interpretation: mechanisms, tensions, disagreement map
4. Confidence, limits, and blind spots
5. Prioritized actions and follow-up questions

Match the user's language in the final report unless they ask otherwise.

## Failure Modes

- If search results are thin, broaden the query semantically instead of pretending there is no discussion.
- If results are noisy, tighten the question, add a submolt filter, or reduce `--max-posts`.
- If a topic is too fresh, say the corpus is early and separate signal from speculation.
- If you only have search hits and no expanded posts yet, do not write a confident report.
- If some post IDs return `404` during expansion, keep going with remaining posts and report these skips from `diagnostics.warnings`.

## References

Read `references/api.md` when you need endpoint behavior, live observations, or query design guidance.
