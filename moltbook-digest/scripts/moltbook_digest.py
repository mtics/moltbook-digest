#!/usr/bin/env python3
"""Build a Moltbook evidence pack for keyword-driven research."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
        help="Directory for brief.md and evidence.json. Defaults to output/moltbook-digest/<timestamp>-<slug>.",
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
    return parser.parse_args()


def clean_text(value: Any) -> str:
    text = value or ""
    text = TAG_RE.sub("", str(text))
    return unescape(text).strip()


def one_line(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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
) -> dict[str, Any]:
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(format_http_error(exc.code, message, url)) from exc
    except URLError as exc:
        raise SystemExit(f"Network error while requesting {url}: {exc.reason}") from exc


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


def collect_search_hits(args: argparse.Namespace) -> list[dict[str, Any]]:
    all_hits: list[dict[str, Any]] = []

    for query in args.queries:
        cursor = None
        for _ in range(args.pages):
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
        "comment_count": post.get("comment_count", 0),
        "verification_status": post.get("verification_status"),
        "author": {
            "id": author.get("id"),
            "name": clean_text(author.get("name")),
            "description": clean_text(author.get("description")),
            "karma": author.get("karma"),
            "follower_count": author.get("followerCount"),
            "following_count": author.get("followingCount"),
        },
        "submolt": {
            "id": submolt.get("id"),
            "name": clean_text(submolt.get("name")),
            "display_name": clean_text(submolt.get("display_name")),
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


def expand_posts(args: argparse.Namespace, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    allowed_submolts = {name.lower() for name in args.submolts}

    for candidate in candidates:
        if len(selected) >= args.max_posts:
            break

        post_payload = api_get(
            args.base_url,
            f"/posts/{candidate['post_id']}",
            None,
            args.api_key,
            args.timeout,
        )
        post = post_payload.get("post") or {}
        submolt_name = clean_text(((post.get("submolt") or {}).get("name"))).lower()
        if allowed_submolts and submolt_name not in allowed_submolts:
            continue

        comments_payload = api_get(
            args.base_url,
            f"/posts/{candidate['post_id']}/comments",
            {"sort": args.comment_sort, "limit": args.comment_limit},
            args.api_key,
            args.timeout,
        )
        cleaned_tree = sanitize_comment_tree(comments_payload.get("comments", []))
        flat_comments = flatten_comments(cleaned_tree)

        selected.append(
            {
                "post": sanitize_post(post, candidate),
                "comments": {
                    "sort": comments_payload.get("sort", args.comment_sort),
                    "count": comments_payload.get("count", len(cleaned_tree)),
                    "has_more": comments_payload.get("has_more", False),
                    "items": cleaned_tree,
                    "samples": select_comment_samples(flat_comments),
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


def render_markdown(pack: dict[str, Any]) -> str:
    lines: list[str] = []
    stats = pack["stats"]

    lines.append("# Moltbook Research Pack")
    lines.append("")
    lines.append(f"- Generated at: `{pack['generated_at']}`")
    lines.append(f"- Queries: {', '.join(f'`{query}`' for query in stats['queries'])}")
    lines.append(f"- Search type: `{stats['search_type']}`")
    lines.append(f"- Raw search hits: `{stats['raw_search_hits']}`")
    lines.append(f"- Unique posts from hits: `{stats['unique_posts_from_hits']}`")
    lines.append(f"- Expanded posts: `{stats['selected_posts']}`")
    lines.append("")
    lines.append("## Scope Notes")
    lines.append("")
    if stats["submolt_filter"]:
        lines.append(f"- Submolt filter: {', '.join(f'`{name}`' for name in stats['submolt_filter'])}")
    else:
        lines.append("- Submolt filter: none")
    lines.append(f"- Pages per query: `{stats['pages_per_query']}`")
    lines.append(f"- Comment sort and limit: `{stats['comment_sort']}` / `{stats['comment_limit']}`")
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
    lines.append("## Expanded Posts")
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
        lines.append("Representative comments:")
        if comments["samples"]:
            for sample in comments["samples"]:
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

    lines.append("## Files")
    lines.append("")
    lines.append("- Full normalized corpus: `evidence.json`")
    lines.append("- Analysis-ready markdown corpus: `brief.md`")
    lines.append("")
    return "\n".join(lines)


def default_output_dir(queries: list[str]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    slug = slugify("-".join(queries)[:48])
    return Path("output") / "moltbook-digest" / f"{stamp}-{slug}"


def write_outputs(pack: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "evidence.json"
    brief_path = output_dir / "brief.md"
    json_path.write_text(json.dumps(pack, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    brief_path.write_text(render_markdown(pack) + "\n", encoding="utf-8")
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


def main() -> int:
    args = parse_args()
    validate_args(args)

    search_hits = collect_search_hits(args)
    if not search_hits:
        raise SystemExit("No search hits returned. Try broader or more descriptive queries.")

    candidates = build_post_candidates(search_hits)
    expanded_posts = expand_posts(args, candidates)
    if not expanded_posts:
        raise SystemExit("No posts matched the current filters after expansion. Try removing the submolt filter or broadening the query.")

    pack = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_base_url": args.base_url,
        "stats": build_stats(args, search_hits, expanded_posts),
        "search_hits": search_hits,
        "posts": expanded_posts,
    }

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(args.queries)
    json_path, brief_path = write_outputs(pack, output_dir)

    print(f"Wrote {brief_path}")
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
