#!/usr/bin/env python3
"""Build a Moltbook evidence pack for keyword-driven research."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://www.moltbook.com/api/v1"
DEFAULT_SITE_URL = "https://www.moltbook.com"
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
DEFAULT_ANALYSIS_SYSTEM_PROMPT = (
    "You are a rigorous Moltbook research analyst. Use only the provided evidence, avoid hallucinations, "
    "distinguish facts from inference, and explicitly call out uncertainty and data limits."
)
DEFAULT_ANALYSIS_LANGUAGE = "zh-CN"
DEFAULT_ANALYSIS_QUESTION_TEMPLATE = (
    "Analyze Moltbook discussions around: {queries}. "
    "Extract core themes, disagreements, risks, and practical actions."
)
DEFAULT_ANALYSIS_PROMPT_TEMPLATE = """Research question: {analysis_question}
User preferred language: {analysis_language}
Hard requirement: Write the final report in {analysis_language}.

You must follow this workflow:
1) First summarize the corpus before deep analysis:
   - For each key post, extract its central claim and evidence style.
   - Then extract common patterns and unique differences across posts.
   - Organize per-post summaries in a Related Work style.
   - For each work, cover problem setting, approach, contribution, and limitations.
   - Explain how each work relates to the user problem and to other works.
   - Use clickable markdown links for each work heading, pointing to the original post URL.
   - Do not use placeholder labels like Work A, Work B, Work C without post links.
   - Write per-post summaries in natural human language with clear flow.
   - Use simple wording and varied sentence length.
   - Avoid repetitive AI-style phrasing and unnecessary jargon.
   - Do not use the em dash punctuation.
2) Then apply first-principles reasoning:
   - Restate the underlying user problem from goals/constraints/tradeoffs.
   - Do not assume the user is fully clear about goals or path.
   - If intent is ambiguous, explicitly list assumptions and alternative interpretation paths.
3) Then perform deep interpretation:
   - Separate evidence from inference.
   - Explain causal mechanisms, not just conclusions.
   - Highlight contradictions, blind spots, and confidence limits.
4) End with actionable recommendations:
   - Prioritized actions.
   - What to ask the user next if key assumptions remain uncertain.

Recommended structure:
{report_structure}

Evidence corpus:
{analysis_input}
"""
DEFAULT_REPORT_STRUCTURE = "\n".join(
    [
        "1. Corpus summary (per-post key points + commonalities + unique differences)",
        "2. First-principles problem framing (goal, constraints, success criteria, assumptions)",
        "3. Deep interpretation (mechanisms, tradeoffs, contradictions)",
        "4. Confidence, blind spots, and uncertainty management",
        "5. Prioritized actions and clarifying questions for the user",
    ]
)
DEFAULT_LLM_CONFIG_PATH = "config.yaml"
SUPPORTED_PROVIDERS = (
    "agent",
    "openai",
    "claude",
    "gemini",
    "siliconflow",
    "minimax",
    "volcengine",
)
PROVIDER_DEFAULTS = {
    "agent": {"analysis_mode": "agent"},
    "openai": {"analysis_mode": "litellm", "model": "openai/gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY"},
    "claude": {
        "analysis_mode": "litellm",
        "model": "anthropic/claude-3-7-sonnet-latest",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {"analysis_mode": "litellm", "model": "gemini/gemini-2.0-flash", "api_key_env": "GEMINI_API_KEY"},
    "siliconflow": {
        "analysis_mode": "litellm",
        "model": "openai/Qwen/Qwen2.5-72B-Instruct",
        "api_key_env": "SILICONFLOW_API_KEY",
        "api_base": "https://api.siliconflow.cn/v1",
    },
    "minimax": {
        "analysis_mode": "litellm",
        "model": "openai/MiniMax-Text-01",
        "api_key_env": "MINIMAX_API_KEY",
        "api_base": "https://api.minimax.chat/v1",
    },
    "volcengine": {
        "analysis_mode": "litellm",
        "model": "openai/doubao-1.5-pro-32k-250115",
        "api_key_env": "ARK_API_KEY",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    },
}


class ApiRequestError(RuntimeError):
    """Recoverable API request error for per-item fault tolerance."""


def init_diagnostics() -> dict[str, Any]:
    return {
        "search_request_failures": 0,
        "post_fetch_failures": 0,
        "comment_fetch_failures": 0,
        "warnings": [],
    }


def add_warning(diagnostics: dict[str, Any], message: str) -> None:
    warnings = diagnostics.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Moltbook search hits, expanded posts, and comment context.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        required=True,
        help="Semantic search query. Repeat for broader coverage.",
    )
    parser.add_argument(
        "--type",
        choices=("all", "posts", "comments"),
        default="all",
        help="What to search.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Search results per page and per query. Max 50.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Pages to fetch per query.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=5,
        help="Maximum expanded posts to include in the evidence pack.",
    )
    parser.add_argument(
        "--comment-limit",
        type=int,
        default=10,
        help="Top-level comments to request per selected post.",
    )
    parser.add_argument(
        "--comment-sort",
        choices=("best", "new", "old"),
        default="best",
        help="Comment sort order for expanded posts.",
    )
    parser.add_argument(
        "--submolt",
        action="append",
        dest="submolts",
        default=[],
        help="Optional submolt filter. Repeat to allow multiple submolts.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for output files. Defaults to output/moltbook-digest/<timestamp>-<slug>.",
    )
    parser.add_argument(
        "--digest-name",
        default="digest.md",
        help="Filename for the unified markdown digest output.",
    )
    parser.add_argument(
        "--evidence-name",
        default="evidence.json",
        help="Filename for structured JSON evidence output.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MOLTBOOK_API_BASE", DEFAULT_BASE_URL),
        help="Override the API base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MOLTBOOK_API_KEY"),
        help="Optional API key. Read-only endpoints currently work without one.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--analysis-mode",
        choices=("none", "litellm", "agent", "auto"),
        default="none",
        help="How to interpret collected content: none, litellm, agent, or auto.",
    )
    parser.add_argument(
        "--analysis-question",
        help="Optional research question the interpretation should answer.",
    )
    parser.add_argument(
        "--analysis-language",
        default=None,
        help="Preferred language for analysis output. If omitted, uses analysis.default_language from config.",
    )
    parser.add_argument(
        "--analysis-input-name",
        default="analysis_input.md",
        help="Legacy filename for structured analysis context (written only with --emit-legacy-analysis-files).",
    )
    parser.add_argument(
        "--analysis-output-name",
        default="analysis_report.md",
        help="Filename for LLM-generated analysis report.",
    )
    parser.add_argument(
        "--agent-handoff-name",
        default="agent_handoff.md",
        help="Legacy filename for agent handoff prompt (written only with --emit-legacy-analysis-files).",
    )
    parser.add_argument(
        "--emit-legacy-analysis-files",
        action="store_true",
        help="Also write legacy analysis_input.md and agent_handoff.md files for backward compatibility.",
    )
    parser.add_argument(
        "--analysis-comment-evidence-limit",
        type=int,
        default=12,
        help="Representative comments per post for analysis context.",
    )
    parser.add_argument(
        "--analysis-post-char-limit",
        type=int,
        default=12000,
        help="Character budget per post body in LLM mode; use 0 for no cap.",
    )
    parser.add_argument(
        "--analysis-context-char-limit",
        type=int,
        default=180000,
        help="Total character budget for LLM input context; use 0 for no cap.",
    )
    parser.add_argument(
        "--litellm-model",
        default=os.environ.get("LITELLM_MODEL"),
        help="Model name passed to LiteLLM, e.g. openai/gpt-4.1-mini.",
    )
    parser.add_argument(
        "--litellm-temperature",
        type=float,
        default=0.2,
        help="Temperature for LiteLLM completion.",
    )
    parser.add_argument(
        "--litellm-max-tokens",
        type=int,
        default=2800,
        help="Max output tokens for LiteLLM completion.",
    )
    parser.add_argument(
        "--litellm-system-prompt",
        default=os.environ.get("MOLTBOOK_ANALYSIS_SYSTEM_PROMPT", DEFAULT_ANALYSIS_SYSTEM_PROMPT),
        help="System prompt used by LiteLLM analysis mode.",
    )
    parser.add_argument(
        "--llm-config",
        "--config",
        dest="llm_config",
        default=DEFAULT_LLM_CONFIG_PATH,
        help="Path to config.yaml (used to resolve provider defaults and prompt template).",
    )
    parser.add_argument(
        "--active-provider",
        choices=SUPPORTED_PROVIDERS,
        help="Override provider from config file. If omitted, uses active_provider in config.",
    )
    return parser.parse_args()


def clean_text(value: Any) -> str:
    text = value or ""
    text = TAG_RE.sub("", str(text))
    return unescape(text).strip()


def one_line(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def is_secret_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return text.startswith("<") and text.endswith(">")


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "PyYAML is required to read llm config. Install dependencies with uv "
            "(for example: uv sync --project moltbook-digest)."
        ) from exc

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Failed to parse llm config {path}: {exc}") from exc

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SystemExit(f"llm config {path} must be a mapping at the top level.")
    return payload


def resolve_active_provider(args: argparse.Namespace, llm_config: dict[str, Any]) -> str:
    if args.active_provider:
        return args.active_provider

    top_level = llm_config.get("active_provider")
    if isinstance(top_level, str) and top_level in SUPPORTED_PROVIDERS:
        return top_level

    defaults = llm_config.get("defaults") or {}
    default_provider = defaults.get("active_provider")
    if isinstance(default_provider, str) and default_provider in SUPPORTED_PROVIDERS:
        return default_provider

    return "agent"


def get_provider_config(llm_config: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = llm_config.get("providers") or {}
    if isinstance(providers, dict):
        config = providers.get(provider) or {}
        if isinstance(config, dict):
            return config
    return {}


def resolve_provider_runtime(args: argparse.Namespace, llm_config: dict[str, Any]) -> dict[str, Any]:
    provider = resolve_active_provider(args, llm_config)
    preset = PROVIDER_DEFAULTS.get(provider, {})
    provider_cfg = get_provider_config(llm_config, provider)
    analysis_cfg = llm_config.get("analysis") if isinstance(llm_config.get("analysis"), dict) else {}
    runtime_mode = args.analysis_mode
    if runtime_mode == "auto":
        runtime_mode = preset.get("analysis_mode", "none")

    model = args.litellm_model or provider_cfg.get("model") or preset.get("model")
    api_base = provider_cfg.get("api_base") or preset.get("api_base")
    api_key_env = provider_cfg.get("api_key_env") or preset.get("api_key_env")
    raw_api_key = provider_cfg.get("api_key")
    api_key = None if is_secret_placeholder(raw_api_key) else str(raw_api_key).strip()
    if not api_key and isinstance(api_key_env, str) and api_key_env:
        api_key = os.environ.get(api_key_env)

    system_prompt = args.litellm_system_prompt
    cfg_system_prompt = provider_cfg.get("system_prompt")
    if cfg_system_prompt and args.litellm_system_prompt == DEFAULT_ANALYSIS_SYSTEM_PROMPT:
        system_prompt = str(cfg_system_prompt)
    prompt_template = analysis_cfg.get("prompt_template") or provider_cfg.get("prompt_template")
    if not prompt_template:
        prompt_template = DEFAULT_ANALYSIS_PROMPT_TEMPLATE
    question_template = analysis_cfg.get("question_template")
    if not isinstance(question_template, str) or not question_template.strip():
        question_template = DEFAULT_ANALYSIS_QUESTION_TEMPLATE
    report_structure = analysis_cfg.get("report_structure")
    if not isinstance(report_structure, str) or not report_structure.strip():
        report_structure = DEFAULT_REPORT_STRUCTURE
    default_language = analysis_cfg.get("default_language")
    if not isinstance(default_language, str) or not default_language.strip():
        default_language = DEFAULT_ANALYSIS_LANGUAGE

    return {
        "provider": provider,
        "analysis_mode": runtime_mode,
        "litellm_model": model,
        "litellm_api_base": api_base,
        "litellm_api_key": api_key,
        "litellm_api_key_env": api_key_env,
        "litellm_system_prompt": system_prompt,
        "analysis_prompt_template": str(prompt_template),
        "analysis_question_template": str(question_template),
        "analysis_report_structure": str(report_structure),
        "analysis_default_language": str(default_language),
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "query"


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def api_get(
    base_url: str,
    path: str,
    params: dict[str, Any] | None,
    api_key: str | None,
    timeout: int,
    retries: int = 2,
) -> dict[str, Any]:
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    transient_http_codes = {408, 425, 429, 500, 502, 503, 504}
    backoff_seconds = 0.8

    for attempt in range(retries + 1):
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            should_retry = exc.code in transient_http_codes and attempt < retries
            if should_retry:
                time.sleep(backoff_seconds * (2**attempt))
                continue
            raise ApiRequestError(format_http_error(exc.code, message, url)) from exc
        except URLError as exc:
            should_retry = attempt < retries
            if should_retry:
                time.sleep(backoff_seconds * (2**attempt))
                continue
            raise ApiRequestError(f"Network error while requesting {url}: {exc.reason}") from exc

    raise ApiRequestError(f"Request failed after retries: {url}")


def format_http_error(code: int, body: str, url: str) -> str:
    cleaned = body.strip()
    if cleaned.startswith("{"):
        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                error = payload.get("error") or payload.get("message") or cleaned
                hint = payload.get("hint")
                if hint:
                    return f"HTTP {code} for {url}: {error}. Hint: {hint}"
                return f"HTTP {code} for {url}: {error}"
        except json.JSONDecodeError:
            pass
    return f"HTTP {code} for {url}: {clip(cleaned, 280)}"


def normalize_hit(hit: dict[str, Any], query: str) -> dict[str, Any]:
    score = hit.get("similarity")
    if score is None:
        score = hit.get("relevance")

    post_id = hit.get("post_id") or hit.get("id")
    relative_url = hit.get("url")
    if relative_url:
        url = relative_url if relative_url.startswith("http") else f"{DEFAULT_SITE_URL}{relative_url}"
    else:
        url = f"{DEFAULT_SITE_URL}/post/{post_id}"

    return {
        "id": hit.get("id"),
        "type": hit.get("type"),
        "query": query,
        "title": clean_text(hit.get("title")),
        "content": clean_text(hit.get("content")),
        "score": score,
        "created_at": hit.get("created_at"),
        "post_id": post_id,
        "url": url,
        "author_name": clean_text((hit.get("author") or {}).get("name")),
        "submolt_name": clean_text((hit.get("submolt") or {}).get("name")),
        "submolt_display_name": clean_text((hit.get("submolt") or {}).get("display_name")),
        "post_title": clean_text((hit.get("post") or {}).get("title")),
    }


def collect_search_hits(args: argparse.Namespace, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    all_hits: list[dict[str, Any]] = []

    for query in args.queries:
        cursor = None
        for page_index in range(args.pages):
            try:
                payload = api_get(
                    args.base_url,
                    "/search",
                    {
                        "q": query,
                        "type": args.type,
                        "limit": min(max(args.limit, 1), 50),
                        "cursor": cursor,
                    },
                    args.api_key,
                    args.timeout,
                )
            except ApiRequestError as exc:
                diagnostics["search_request_failures"] = int(diagnostics.get("search_request_failures", 0)) + 1
                add_warning(
                    diagnostics,
                    f"Search request failed for query `{query}` page `{page_index + 1}`: {exc}",
                )
                break

            for hit in payload.get("results", []):
                all_hits.append(normalize_hit(hit, query))

            if not payload.get("has_more") or not payload.get("next_cursor"):
                break
            cursor = payload["next_cursor"]

    return all_hits


def build_post_candidates(search_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    for hit in search_hits:
        post_id = hit["post_id"]
        if not post_id:
            continue

        candidate = candidates.setdefault(
            post_id,
            {
                "post_id": post_id,
                "best_score": float("-inf"),
                "matched_queries": set(),
                "search_hits": [],
                "latest_hit_at": hit.get("created_at"),
            },
        )
        candidate["best_score"] = max(candidate["best_score"], float(hit.get("score") or 0.0))
        candidate["matched_queries"].add(hit["query"])
        candidate["search_hits"].append(hit)

        latest = hit.get("created_at")
        if parse_iso(latest) > parse_iso(candidate.get("latest_hit_at")):
            candidate["latest_hit_at"] = latest

    ranked = []
    for candidate in candidates.values():
        candidate["matched_queries"] = sorted(candidate["matched_queries"])
        candidate["search_hits"] = sorted(
            candidate["search_hits"],
            key=lambda item: (float(item.get("score") or 0.0), parse_iso(item.get("created_at"))),
            reverse=True,
        )
        ranked.append(candidate)

    ranked.sort(
        key=lambda item: (
            len(item["matched_queries"]),
            float(item["best_score"]),
            parse_iso(item.get("latest_hit_at")),
        ),
        reverse=True,
    )
    return ranked


def sanitize_post(post: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    author = post.get("author") or {}
    submolt = post.get("submolt") or {}
    return {
        "id": post.get("id"),
        "title": clean_text(post.get("title")),
        "content": clean_text(post.get("content")),
        "type": post.get("type"),
        "created_at": post.get("created_at"),
        "updated_at": post.get("updated_at"),
        "upvotes": post.get("upvotes", 0),
        "downvotes": post.get("downvotes", 0),
        "score": post.get("score", 0),
        "comment_count": post.get("comment_count", post.get("comments_count", 0)),
        "verification_status": post.get("verification_status"),
        "author": {
            "id": author.get("id"),
            "name": clean_text(author.get("name")),
            "description": clean_text(author.get("description")),
            "karma": author.get("karma"),
            "follower_count": author.get("followerCount", author.get("follower_count")),
            "following_count": author.get("followingCount", author.get("following_count")),
        },
        "submolt": {
            "id": submolt.get("id"),
            "name": clean_text(submolt.get("name")),
            "display_name": clean_text(submolt.get("display_name", submolt.get("displayName"))),
        },
        "url": f"{DEFAULT_SITE_URL}/post/{post.get('id')}",
        "matched_queries": evidence["matched_queries"],
        "best_match_score": evidence["best_score"],
        "search_hits": evidence["search_hits"][:5],
    }


def sanitize_comment_tree(comments: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    cleaned = []
    for comment in comments:
        author = comment.get("author") or {}
        replies = comment.get("replies") or []
        cleaned.append(
            {
                "id": comment.get("id"),
                "content": clean_text(comment.get("content")),
                "created_at": comment.get("created_at"),
                "upvotes": comment.get("upvotes", 0),
                "downvotes": comment.get("downvotes", 0),
                "score": comment.get("score", 0),
                "depth": depth,
                "author": {
                    "id": author.get("id"),
                    "name": clean_text(author.get("name")),
                },
                "replies": sanitize_comment_tree(replies, depth + 1),
            }
        )
    return cleaned


def flatten_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []

    def _walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            entry = {
                "id": node.get("id"),
                "content": node.get("content"),
                "created_at": node.get("created_at"),
                "upvotes": node.get("upvotes", 0),
                "downvotes": node.get("downvotes", 0),
                "score": node.get("score", 0),
                "depth": node.get("depth", 0),
                "author_name": clean_text((node.get("author") or {}).get("name")),
            }
            flat.append(entry)
            _walk(node.get("replies") or [])

    _walk(comments)
    return flat


def select_comment_samples(flat_comments: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not flat_comments:
        return []

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_score = sorted(
        flat_comments,
        key=lambda item: (item.get("score", 0), parse_iso(item.get("created_at"))),
        reverse=True,
    )
    by_newest = sorted(flat_comments, key=lambda item: parse_iso(item.get("created_at")), reverse=True)

    for pool in (by_score, by_newest):
        for item in pool:
            if not item["content"] or item["id"] in seen:
                continue
            selected.append(
                {
                    "id": item["id"],
                    "author_name": item["author_name"],
                    "content": item["content"],
                    "created_at": item["created_at"],
                    "score": item["score"],
                    "depth": item["depth"],
                }
            )
            seen.add(item["id"])
            if len(selected) >= limit:
                return selected

    return selected


def expand_posts(
    args: argparse.Namespace,
    candidates: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    allowed_submolts = {name.lower() for name in args.submolts}

    for candidate in candidates:
        if len(selected) >= args.max_posts:
            break

        try:
            post_payload = api_get(
                args.base_url,
                f"/posts/{candidate['post_id']}",
                None,
                args.api_key,
                args.timeout,
            )
        except ApiRequestError as exc:
            diagnostics["post_fetch_failures"] = int(diagnostics.get("post_fetch_failures", 0)) + 1
            add_warning(diagnostics, f"Skipped post `{candidate['post_id']}` because post detail fetch failed: {exc}")
            continue

        post = post_payload.get("post") or {}
        submolt_name = clean_text(((post.get("submolt") or {}).get("name"))).lower()
        if allowed_submolts and submolt_name not in allowed_submolts:
            continue

        try:
            comments_payload = api_get(
                args.base_url,
                f"/posts/{candidate['post_id']}/comments",
                {"sort": args.comment_sort, "limit": args.comment_limit},
                args.api_key,
                args.timeout,
            )
        except ApiRequestError as exc:
            diagnostics["comment_fetch_failures"] = int(diagnostics.get("comment_fetch_failures", 0)) + 1
            add_warning(diagnostics, f"Post `{candidate['post_id']}` comments fetch failed; continuing with empty comments: {exc}")
            comments_payload = {"sort": args.comment_sort, "count": 0, "has_more": False, "comments": []}

        cleaned_tree = sanitize_comment_tree(comments_payload.get("comments", []))
        raw_comment_count = comments_payload.get("count")
        comment_count = raw_comment_count if isinstance(raw_comment_count, int) else len(cleaned_tree)

        selected.append(
            {
                "post": sanitize_post(post, candidate),
                "comments": {
                    "sort": comments_payload.get("sort", args.comment_sort),
                    "count": comment_count,
                    "has_more": comments_payload.get("has_more", False),
                    "items": cleaned_tree,
                },
            }
        )

    return selected


def build_stats(
    args: argparse.Namespace,
    search_hits: list[dict[str, Any]],
    selected_posts: list[dict[str, Any]],
) -> dict[str, Any]:
    submolt_counts: Counter[str] = Counter()
    author_counts: Counter[str] = Counter()
    created_ats: list[str] = []

    for item in selected_posts:
        post = item["post"]
        submolt = post["submolt"]["name"] or "unknown"
        author = post["author"]["name"] or "unknown"
        submolt_counts[submolt] += 1
        author_counts[author] += 1
        if post.get("created_at"):
            created_ats.append(post["created_at"])

    time_range = None
    if created_ats:
        ordered = sorted(created_ats, key=parse_iso)
        time_range = {"earliest": ordered[0], "latest": ordered[-1]}

    return {
        "queries": args.queries,
        "search_type": args.type,
        "pages_per_query": args.pages,
        "limit_per_page": args.limit,
        "requested_max_posts": args.max_posts,
        "comment_limit": args.comment_limit,
        "comment_sort": args.comment_sort,
        "submolt_filter": args.submolts,
        "raw_search_hits": len(search_hits),
        "unique_posts_from_hits": len({hit["post_id"] for hit in search_hits if hit.get("post_id")}),
        "selected_posts": len(selected_posts),
        "top_submolts": submolt_counts.most_common(10),
        "top_authors": author_counts.most_common(10),
        "time_range": time_range,
    }


def render_markdown(
    pack: dict[str, Any],
    args: argparse.Namespace,
    runtime: dict[str, Any],
    digest_name: str,
    evidence_name: str,
) -> str:
    lines: list[str] = []
    stats = pack["stats"]
    diagnostics = pack.get("diagnostics") or {}
    warnings = diagnostics.get("warnings") if isinstance(diagnostics.get("warnings"), list) else []
    mode = runtime.get("analysis_mode") or "none"

    lines.append("# Moltbook Digest")
    lines.append("")
    lines.append("## Run Summary")
    lines.append("")
    lines.append(f"- Generated at: `{pack['generated_at']}`")
    lines.append(f"- Analysis mode: `{mode}`")
    lines.append(f"- Provider routing: `{runtime.get('provider')}`")
    lines.append(f"- Queries: {', '.join(f'`{query}`' for query in stats['queries'])}")
    lines.append(f"- Search type: `{stats['search_type']}`")
    lines.append(f"- Raw search hits: `{stats['raw_search_hits']}`")
    lines.append(f"- Unique posts from hits: `{stats['unique_posts_from_hits']}`")
    lines.append(f"- Expanded posts: `{stats['selected_posts']}`")
    lines.append("")
    lines.append("## Collection Diagnostics")
    lines.append("")
    lines.append(f"- Search request failures: `{diagnostics.get('search_request_failures', 0)}`")
    lines.append(f"- Post detail fetch failures: `{diagnostics.get('post_fetch_failures', 0)}`")
    lines.append(f"- Comment fetch failures: `{diagnostics.get('comment_fetch_failures', 0)}`")
    if warnings:
        lines.append("Non-fatal warnings:")
        for warning in warnings[:8]:
            lines.append(f"- Warning: {warning}")
        remaining = len(warnings) - 8
        if remaining > 0:
            lines.append(f"- ... and `{remaining}` additional warnings")
    else:
        lines.append("- Non-fatal warnings: none")
    lines.append("")
    lines.append("## Scope & Coverage")
    lines.append("")
    if stats["submolt_filter"]:
        lines.append(f"- Submolt filter: {', '.join(f'`{name}`' for name in stats['submolt_filter'])}")
    else:
        lines.append("- Submolt filter: none")
    lines.append(f"- Pages per query: `{stats['pages_per_query']}`")
    lines.append(f"- Comment sort and limit: `{stats['comment_sort']}` / `{stats['comment_limit']}`")
    if stats["top_submolts"]:
        lines.append("- Top submolts: " + ", ".join(f"`{name}` ({count})" for name, count in stats["top_submolts"][:5]))
    if stats["top_authors"]:
        lines.append("- Top authors: " + ", ".join(f"`{name}` ({count})" for name, count in stats["top_authors"][:5]))
    if stats["time_range"]:
        lines.append(
            f"- Time range across expanded posts: `{stats['time_range']['earliest']}` to `{stats['time_range']['latest']}`"
        )
    lines.append("")
    lines.append("## Suggested Analytical Questions")
    lines.append("")
    lines.append("- What themes recur across multiple posts instead of appearing only once?")
    lines.append("- Which claims survive contact with the comment threads?")
    lines.append("- Where do authors disagree because of different assumptions rather than different facts?")
    lines.append("- What important perspectives are still missing from this sample?")
    lines.append("")
    lines.append("## Evidence Posts")
    lines.append("")

    for index, item in enumerate(pack["posts"], start=1):
        post = item["post"]
        comments = item["comments"]
        lines.append(f"### {index}. {post['title'] or '(untitled post)'}")
        lines.append("")
        lines.append(f"- URL: {post['url']}")
        lines.append(f"- Author: `{post['author']['name'] or 'unknown'}`")
        lines.append(f"- Submolt: `{post['submolt']['name'] or 'unknown'}`")
        lines.append(f"- Created at: `{post['created_at']}`")
        lines.append(f"- Score / comments: `{post['score']}` / `{post['comment_count']}`")
        lines.append(f"- Matched queries: {', '.join(f'`{query}`' for query in post['matched_queries'])}")
        lines.append(f"- Best match score: `{post['best_match_score']}`")
        lines.append("")
        lines.append("Search evidence:")
        for hit in post["search_hits"]:
            excerpt = one_line(hit["content"] or hit["title"] or hit["post_title"])
            lines.append(
                f"- [{hit['type']}] query=`{hit['query']}` score=`{hit['score']}` excerpt={clip(excerpt, 180)}"
            )
        lines.append("")
        lines.append("Post body:")
        lines.append("")
        lines.append("```text")
        lines.append(post["content"] or "")
        lines.append("```")
        lines.append("")
        lines.append(
            "Representative comments "
            f"(sampled from `{comments.get('count', 0)}` comments, capped at `{args.analysis_comment_evidence_limit}`):"
        )
        sampled_comments = select_analysis_comments(comments["items"], args.analysis_comment_evidence_limit)
        if sampled_comments:
            for sample in sampled_comments:
                lines.append(
                    f"- depth={sample['depth']} score={sample['score']} author=`{sample['author_name'] or 'unknown'}`"
                )
                lines.append("")
                lines.append("```text")
                lines.append(sample["content"] or "")
                lines.append("```")
        else:
            lines.append("- No comments sampled.")
        lines.append("")

    if mode == "agent":
        prompt_preview = build_runtime_analysis_prompt(
            args,
            runtime,
            (
                f"Use evidence from `{digest_name}` and `{evidence_name}`. "
                f"If present, `{args.analysis_input_name}` can be used as the structured corpus snapshot."
            ),
        )
        lines.append("## Agent Task Card")
        lines.append("")
        lines.append("Read this digest and then write the final report.")
        lines.append("")
        lines.append(f"- Target output file: `{args.analysis_output_name}`")
        lines.append(f"- Output language: `{args.analysis_language}`")
        lines.append(
            "- Analysis instructions are shared with LiteLLM mode and come from `analysis.prompt_template`."
        )
        lines.append("Configured prompt preview:")
        lines.append("")
        lines.append("```text")
        lines.append(prompt_preview)
        lines.append("```")
        lines.append("")

    lines.append("## Output Files")
    lines.append("")
    lines.append(f"- Unified markdown digest: `{digest_name}`")
    lines.append(f"- Full normalized corpus: `{evidence_name}`")
    if mode == "litellm":
        lines.append(f"- LLM-generated report: `{args.analysis_output_name}`")
    if mode == "agent":
        lines.append(f"- Agent-generated report target: `{args.analysis_output_name}`")
    lines.append("")
    return "\n".join(lines)


def resolve_analysis_question(args: argparse.Namespace, runtime: dict[str, Any] | None = None) -> str:
    if args.analysis_question:
        return clean_text(args.analysis_question)
    template = DEFAULT_ANALYSIS_QUESTION_TEMPLATE
    if runtime and isinstance(runtime.get("analysis_question_template"), str):
        template = runtime["analysis_question_template"]
    queries = ", ".join(args.queries)
    try:
        return clean_text(template.format(queries=queries))
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise SystemExit(
            "Invalid analysis.question_template in config: missing placeholder "
            f"{{{missing}}}. Allowed placeholder is {{queries}}."
        ) from exc


def apply_char_cap(text: str, limit: int, label: str) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    capped = text[:limit].rstrip()
    note = f"\n\n[TRUNCATED {label}: original {len(text)} chars, capped at {limit} chars]"
    return capped + note, True


def select_analysis_comments(comment_tree: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    flat = flatten_comments(comment_tree)
    if not flat:
        return []
    if limit <= 0 or len(flat) <= limit:
        return sorted(
            flat,
            key=lambda item: (item.get("score", 0), parse_iso(item.get("created_at"))),
            reverse=True,
        )
    return select_comment_samples(flat, limit)


def render_analysis_input(
    pack: dict[str, Any],
    args: argparse.Namespace,
    for_litellm: bool,
    runtime: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    stats = pack["stats"]
    question = resolve_analysis_question(args, runtime)

    lines.append("# Moltbook Analysis Input")
    lines.append("")
    lines.append(f"- Research question: {question}")
    lines.append(f"- Preferred report language: `{args.analysis_language}`")
    lines.append(f"- Queries: {', '.join(f'`{query}`' for query in stats['queries'])}")
    lines.append(f"- Expanded posts: `{stats['selected_posts']}`")
    lines.append(f"- Raw search hits: `{stats['raw_search_hits']}`")
    lines.append("")
    lines.append("## Method Reminder")
    lines.append("")
    lines.append("- Use only the evidence in this file.")
    lines.append("- Separate direct evidence from inference.")
    lines.append("- Call out blind spots and confidence limits.")
    lines.append("")
    lines.append("## Evidence Corpus")
    lines.append("")

    for index, item in enumerate(pack["posts"], start=1):
        post = item["post"]
        comments = item["comments"]
        lines.append(f"### Post {index}: {post['title'] or '(untitled)'}")
        lines.append("")
        lines.append(f"- Post ID: `{post['id']}`")
        lines.append(f"- URL: {post['url']}")
        lines.append(f"- Author: `{post['author']['name'] or 'unknown'}`")
        lines.append(f"- Submolt: `{post['submolt']['name'] or 'unknown'}`")
        lines.append(f"- Score / comments: `{post['score']}` / `{post['comment_count']}`")
        lines.append(f"- Matched queries: {', '.join(f'`{query}`' for query in post['matched_queries'])}")
        lines.append("")
        body = post["content"] or ""
        if for_litellm:
            body, _ = apply_char_cap(body, args.analysis_post_char_limit, "post body")
        lines.append("Post body:")
        lines.append("")
        lines.append("```text")
        lines.append(body)
        lines.append("```")
        lines.append("")

        sampled_comments = select_analysis_comments(comments["items"], args.analysis_comment_evidence_limit)
        lines.append(f"Representative comments for analysis (`{len(sampled_comments)}`):")
        if not sampled_comments:
            lines.append("- No comments available.")
            lines.append("")
            continue

        for sample in sampled_comments:
            lines.append(
                f"- Comment `{sample['id']}` depth=`{sample['depth']}` "
                f"score=`{sample['score']}` author=`{sample['author_name'] or 'unknown'}`"
            )
            comment_body = sample["content"] or ""
            if for_litellm:
                comment_body, _ = apply_char_cap(comment_body, 1500, "comment body")
            lines.append("")
            lines.append("```text")
            lines.append(comment_body)
            lines.append("```")
        lines.append("")

    rendered = "\n".join(lines)
    if for_litellm:
        rendered, _ = apply_char_cap(
            rendered,
            args.analysis_context_char_limit,
            "analysis context",
        )
    return rendered


def extract_litellm_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            return str(content or "").strip()

    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        if message:
            content = getattr(message, "content", "")
            return str(content or "").strip()
    return ""


def build_analysis_prompt(
    question: str,
    language: str,
    analysis_input_text: str,
    template: str,
    report_structure: str,
) -> str:
    try:
        return template.format(
            analysis_question=question,
            analysis_language=language,
            report_structure=report_structure,
            analysis_input=analysis_input_text,
        )
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise SystemExit(
            "Invalid prompt_template in config: missing placeholder "
            f"{{{missing}}}. Allowed placeholders are "
            "{analysis_question}, {analysis_language}, {report_structure}, {analysis_input}."
        ) from exc


def build_runtime_analysis_prompt(
    args: argparse.Namespace,
    runtime: dict[str, Any],
    analysis_input_text: str,
) -> str:
    question = resolve_analysis_question(args, runtime)
    return build_analysis_prompt(
        question,
        args.analysis_language,
        analysis_input_text,
        runtime.get("analysis_prompt_template") or DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
        runtime.get("analysis_report_structure") or DEFAULT_REPORT_STRUCTURE,
    )


def run_litellm_analysis(args: argparse.Namespace, prompt: str, runtime: dict[str, Any]) -> str:
    try:
        from litellm import completion  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "LiteLLM is required for LiteLLM analysis. Install dependencies with uv "
            "(for example: uv sync --project moltbook-digest)."
        ) from exc

    try:
        response = completion(
            model=runtime["litellm_model"],
            temperature=args.litellm_temperature,
            max_tokens=args.litellm_max_tokens,
            api_base=runtime.get("litellm_api_base"),
            api_key=runtime.get("litellm_api_key"),
            messages=[
                {"role": "system", "content": runtime.get("litellm_system_prompt") or args.litellm_system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:
        raise SystemExit(f"LiteLLM analysis call failed: {exc}") from exc

    content = extract_litellm_text(response).strip()
    if not content:
        raise SystemExit("LiteLLM returned an empty analysis response.")

    header = "\n".join(
        [
            "# Moltbook Analysis Report",
            "",
            f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
            f"- Mode: `litellm`",
            f"- Provider: `{runtime.get('provider')}`",
            f"- Model: `{runtime.get('litellm_model')}`",
            f"- Language: `{args.analysis_language}`",
            "",
        ]
    )
    return header + content + "\n"


def render_agent_handoff(
    args: argparse.Namespace,
    digest_name: str,
    evidence_name: str,
    runtime: dict[str, Any],
    prompt_text: str,
) -> str:
    question = resolve_analysis_question(args, runtime)
    lines = [
        "# Agent Handoff for Deep Interpretation",
        "",
        "## Objective",
        "",
        question,
        "",
        "## Routing",
        "",
        "- Analysis path: `agent`",
        "- Template policy: shared with `litellm` mode (`analysis.prompt_template` + `analysis.report_structure`)",
        f"- Output target: `{args.analysis_output_name}` in `{args.analysis_language}`",
        "",
        "## Files to read",
        "",
        f"- `{digest_name}`",
        f"- `{evidence_name}`",
        f"- `{args.analysis_input_name}`",
        "",
        "## Unified Prompt (Same as LiteLLM Path)",
        "",
        "```text",
        prompt_text,
        "```",
        "",
    ]
    return "\n".join(lines)


def default_output_dir(queries: list[str]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    slug = slugify("-".join(queries)[:48])
    return Path("output") / "moltbook-digest" / f"{stamp}-{slug}"


def write_outputs(
    pack: dict[str, Any],
    args: argparse.Namespace,
    runtime: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / args.evidence_name
    brief_path = output_dir / args.digest_name
    json_path.write_text(json.dumps(pack, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    brief_path.write_text(
        render_markdown(pack, args, runtime, args.digest_name, args.evidence_name) + "\n",
        encoding="utf-8",
    )
    return json_path, brief_path


def validate_args(args: argparse.Namespace) -> None:
    if args.limit < 1 or args.limit > 50:
        raise SystemExit("--limit must be between 1 and 50")
    if args.pages < 1 or args.pages > 5:
        raise SystemExit("--pages must be between 1 and 5")
    if args.max_posts < 1 or args.max_posts > 25:
        raise SystemExit("--max-posts must be between 1 and 25")
    if args.comment_limit < 1 or args.comment_limit > 100:
        raise SystemExit("--comment-limit must be between 1 and 100")
    if not args.base_url.startswith("https://www.moltbook.com"):
        raise SystemExit("--base-url must point at https://www.moltbook.com")
    if args.analysis_comment_evidence_limit < 1:
        raise SystemExit("--analysis-comment-evidence-limit must be >= 1")
    if args.analysis_post_char_limit < 0:
        raise SystemExit("--analysis-post-char-limit must be >= 0")
    if args.analysis_context_char_limit < 0:
        raise SystemExit("--analysis-context-char-limit must be >= 0")
    if args.litellm_temperature < 0 or args.litellm_temperature > 2:
        raise SystemExit("--litellm-temperature must be between 0 and 2")
    if args.litellm_max_tokens < 64:
        raise SystemExit("--litellm-max-tokens must be >= 64")
    if not str(args.digest_name).strip():
        raise SystemExit("--digest-name must not be empty")
    if not str(args.evidence_name).strip():
        raise SystemExit("--evidence-name must not be empty")
    if args.digest_name == args.evidence_name:
        raise SystemExit("--digest-name and --evidence-name must be different filenames")


def validate_runtime(args: argparse.Namespace, runtime: dict[str, Any]) -> None:
    mode = runtime["analysis_mode"]
    if mode == "litellm" and not runtime.get("analysis_prompt_template"):
        raise SystemExit("LiteLLM mode requires analysis prompt template. Set analysis.prompt_template in config.")
    if mode == "litellm" and not runtime.get("litellm_model"):
        raise SystemExit(
            "LiteLLM mode requires a model. Set --litellm-model or configure model in config providers.<name>.model"
        )
    if mode == "litellm" and runtime.get("provider") != "agent":
        if not runtime.get("litellm_api_key"):
            env_name = runtime.get("litellm_api_key_env") or "provider API key env var"
            raise SystemExit(
                f"{runtime.get('provider')} requires API key. Set providers.{runtime.get('provider')}.api_key "
                f"or export {env_name}."
            )


def main() -> int:
    args = parse_args()
    validate_args(args)
    llm_config = load_yaml_file(Path(args.llm_config))
    runtime = resolve_provider_runtime(args, llm_config)
    if not args.analysis_language:
        args.analysis_language = runtime.get("analysis_default_language") or DEFAULT_ANALYSIS_LANGUAGE
    validate_runtime(args, runtime)
    diagnostics = init_diagnostics()

    search_hits = collect_search_hits(args, diagnostics)
    if not search_hits:
        raise SystemExit("No search hits returned. Try broader or more descriptive queries.")

    candidates = build_post_candidates(search_hits)
    expanded_posts = expand_posts(args, candidates, diagnostics)
    if not expanded_posts:
        raise SystemExit("No posts matched the current filters after expansion. Try removing the submolt filter or broadening the query.")

    pack = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_base_url": args.base_url,
        "runtime": {
            "analysis_mode": runtime.get("analysis_mode"),
            "provider": runtime.get("provider"),
        },
        "stats": build_stats(args, search_hits, expanded_posts),
        "diagnostics": diagnostics,
        "search_hits": search_hits,
        "posts": expanded_posts,
    }

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(args.queries)
    json_path, brief_path = write_outputs(pack, args, runtime, output_dir)
    print(f"Wrote {brief_path}")
    print(f"Wrote {json_path}")
    if diagnostics.get("warnings"):
        print(
            "Completed with non-fatal warnings "
            f"(`{len(diagnostics['warnings'])}` total). See `diagnostics.warnings` in {json_path.name}."
        )

    if runtime["analysis_mode"] != "none":
        legacy_analysis_input_text: str | None = None
        if args.emit_legacy_analysis_files:
            legacy_analysis_input_text = render_analysis_input(pack, args, for_litellm=False, runtime=runtime)
            analysis_input_path = output_dir / args.analysis_input_name
            analysis_input_path.write_text(legacy_analysis_input_text + "\n", encoding="utf-8")
            print(f"Wrote {analysis_input_path}")

        if runtime["analysis_mode"] == "litellm":
            llm_input = render_analysis_input(pack, args, for_litellm=True, runtime=runtime)
            llm_prompt = build_runtime_analysis_prompt(args, runtime, llm_input)
            report_text = run_litellm_analysis(args, llm_prompt, runtime)
            analysis_report_path = output_dir / args.analysis_output_name
            analysis_report_path.write_text(report_text, encoding="utf-8")
            print(f"Wrote {analysis_report_path}")

        if runtime["analysis_mode"] == "agent" and args.emit_legacy_analysis_files:
            if legacy_analysis_input_text is None:
                legacy_analysis_input_text = render_analysis_input(pack, args, for_litellm=False, runtime=runtime)
            handoff_prompt = build_runtime_analysis_prompt(args, runtime, legacy_analysis_input_text)
            handoff_path = output_dir / args.agent_handoff_name
            handoff_path.write_text(
                render_agent_handoff(args, args.digest_name, args.evidence_name, runtime, handoff_prompt) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote {handoff_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
