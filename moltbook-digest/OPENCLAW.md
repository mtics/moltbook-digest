# OpenClaw Setup Guide

This folder is organized as an OpenClaw skill (`SKILL.md` with AgentSkills-compatible frontmatter).
The skill key is `moltbookDigest`.
Reference: https://docs.openclaw.ai/tools/skills

## 1. Install location (choose one)

Option A, workspace-only skill (highest precedence):

```bash
mkdir -p <your-workspace>/skills
rsync -a --exclude ".venv" /path/to/repo/moltbook-digest/ <your-workspace>/skills/moltbook-digest/
```

Option B, shared skill for all local agents:

```bash
mkdir -p ~/.openclaw/skills
rsync -a --exclude ".venv" /path/to/repo/moltbook-digest/ ~/.openclaw/skills/moltbook-digest/
```

If both exist, OpenClaw precedence is:
`<workspace>/skills` > `~/.openclaw/skills` > bundled skills.

## 2. Install dependencies (uv required by skill gating)

```bash
uv sync --project <skill-root>
```

Important:
`metadata.openclaw.requires.bins` checks host `PATH` at skill load time.
If `uv` is installed but not found by OpenClaw, ensure the OpenClaw process can see the bin path (for example `/opt/homebrew/bin` on Apple Silicon macOS).

## 3. Create local config

```bash
cp <skill-root>/config.example.yaml <skill-root>/config.yaml
```

Default route is `active_provider: agent`, so no external API key is required.

## 4. OpenClaw skill entry and env injection

`SKILL.md` sets `metadata.openclaw.skillKey: moltbookDigest`.
Use that key under `skills.entries` in `~/.openclaw/openclaw.json`.

Example:

```json5
{
  skills: {
    entries: {
      moltbookDigest: {
        enabled: true,
        config: {
          note: "optional custom fields if needed"
        }
      }
    }
  }
}
```

## 5. Secret handling policy (important)

API keys must be filled by the user manually, never by OpenClaw/agent.

- Do not ask the agent to write, rewrite, or print API keys.
- If you need external providers, user should fill keys manually in local `config.yaml` or local environment variables.
- Keep `config.yaml` local and uncommitted.

## 6. Refresh behavior

OpenClaw snapshots eligible skills per session. After editing `SKILL.md` or config, start a new session.
If `skills.load.watch` is enabled, updates can be picked up automatically on the next turn.

## 7. Use in OpenClaw

Ask your agent to use `$moltbook-digest`, for example:

- Collect only: run with `--analysis-mode none`
- Agent interpretation: run with `--analysis-mode auto` (with `active_provider: agent`)
- External LLM interpretation: set external `active_provider`, then run with `--analysis-mode auto`
