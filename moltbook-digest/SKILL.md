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
python3 scripts/moltbook_digest.py \
  --query "how agents handle memory" \
  --query "persistent memory architectures for agents" \
  --max-posts 6 \
  --comment-limit 12
```

The script writes:

- `output/moltbook-digest/<timestamp>-<slug>/brief.md`
- `output/moltbook-digest/<timestamp>-<slug>/evidence.json`

`brief.md` should remain analysis-safe: keep full expanded post bodies and full sampled comments there by default. Use it as the markdown corpus for downstream reasoning, not as a teaser summary.

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
pip install litellm
```

Or install from the project file:

```bash
pip install -r requirements.txt
```

Provider config template:

```bash
cp config.example.yaml config.yaml
```

Then fill real keys in `config.yaml` (do not commit it). The repository tracks only `config.example.yaml`.
Prompt template for LiteLLM analysis also lives in `config.yaml` under `analysis.prompt_template`, and should require output in `{analysis_language}`.
If `active_provider` is set to `agent`, no external key is required and interpretation runs in agent mode.
The config file is intentionally minimal. Most provider defaults (such as base URL and key env names) are built into `scripts/moltbook_digest.py`.

Auto-selection behavior:

- If `active_provider=agent` and `--analysis-mode none`, the script automatically runs agent interpretation mode.
- If `active_provider` is an external provider and `--analysis-mode none`, the script automatically runs LiteLLM mode.
- If no provider is configured, `--analysis-mode none` keeps collection-only behavior.

Example:

```bash
python3 scripts/moltbook_digest.py \
  --query "how agents handle memory" \
  --query "persistent memory architectures for agents" \
  --analysis-mode litellm \
  --litellm-model "openai/gpt-4.1-mini" \
  --analysis-question "What durable design patterns and failure modes emerge from these discussions?"
```

Outputs added to the run folder:

- `analysis_input.md` (structured context passed to the model)
- `analysis_report.md` (LLM-generated deep analysis)

Important:

- Keep `--analysis-post-char-limit` and `--analysis-context-char-limit` high enough for deep analysis.
- If the LLM call fails because context is too large, reduce `--max-posts` or `--comment-limit` first, then tune the analysis limits.

### Mode B: Agent (direct interpretation by your agent)

Use this when the user wants their own agent to perform interpretation, without wiring an external LLM API in the script.

Example:

```bash
python3 scripts/moltbook_digest.py \
  --query "agent memory governance" \
  --analysis-mode agent \
  --analysis-question "What are the core governance disagreements and practical policy implications?"
```

Outputs added to the run folder:

- `analysis_input.md` (full analysis context)
- `agent_handoff.md` (copy-paste prompt and report requirements for your agent)

`agent_handoff.md` is intentionally explicit about evidence standards, uncertainty handling, and required report structure.

### 5. Deliver a complete report

Unless the user asks for a different format, structure the report like this:

1. Research brief
2. Method and scope
3. Executive summary
4. Major themes
5. Representative posts and what each contributes
6. Tensions, disagreements, or open questions
7. Confidence, limits, and blind spots
8. Recommended next queries or actions

Match the user's language in the final report unless they ask otherwise.

## Failure Modes

- If search results are thin, broaden the query semantically instead of pretending there is no discussion.
- If results are noisy, tighten the question, add a submolt filter, or reduce `--max-posts`.
- If a topic is too fresh, say the corpus is early and separate signal from speculation.
- If you only have search hits and no expanded posts yet, do not write a confident report.

## References

Read `references/api.md` when you need endpoint behavior, live observations, or query design guidance.
