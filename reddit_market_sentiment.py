#!/usr/bin/env python3
"""Collect stock-related Reddit posts through the official OAuth Data API."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError as exc:
    raise SystemExit("缺少 requests；请先运行: python -m pip install -r requirements.txt") from exc

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_ROOT = "https://oauth.reddit.com"
CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5})(?![A-Za-z])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
POSITIVE = {
    "beat", "beats", "bull", "bullish", "buy", "growth", "gain", "gains",
    "great", "higher", "outperform", "profit", "profits", "rally", "strong",
    "surge", "upside", "upgrade", "winner",
}
NEGATIVE = {
    "bear", "bearish", "crash", "cut", "decline", "downgrade", "drop", "fraud",
    "loss", "losses", "miss", "misses", "risk", "sell", "short", "slump",
    "weak", "warning", "downside",
}


@dataclass
class Post:
    id: str
    created_utc: str
    subreddit: str
    title: str
    text: str
    url: str
    permalink: str
    score: int
    comments: int
    tickers: list[str]
    sentiment_score: float
    sentiment_label: str


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def get_token(session: requests.Session, client_id: str, client_secret: str) -> str:
    response = session.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("OAuth 响应中没有 access_token")
    return str(token)


def api_get(session: requests.Session, path: str, params: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(6):
        response = session.get(f"{API_ROOT}{path}", params=params, timeout=30)
        if response.status_code == 429:
            reset = float(response.headers.get("X-Ratelimit-Reset", "60"))
            time.sleep(min(max(reset, 1), 600))
            continue
        if response.status_code >= 500:
            time.sleep(min(2**attempt, 30))
            continue
        response.raise_for_status()
        remaining = float(response.headers.get("X-Ratelimit-Remaining", "999"))
        if remaining < 2:
            reset = float(response.headers.get("X-Ratelimit-Reset", "1"))
            time.sleep(min(max(reset, 1), 600))
        return response.json()
    raise RuntimeError(f"Reddit API 多次失败: {path}")


def sentiment(text: str) -> tuple[float, str]:
    words = [word.lower() for word in WORD_RE.findall(text)]
    if not words:
        return 0.0, "neutral"
    positive = sum(word in POSITIVE for word in words)
    negative = sum(word in NEGATIVE for word in words)
    score = round((positive - negative) / max(positive + negative, 1), 4)
    label = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"
    return score, label


def parse_post(data: dict[str, Any], requested_tickers: set[str]) -> Post:
    title = str(data.get("title", ""))
    body = str(data.get("selftext", ""))
    detected = {ticker.upper() for ticker in CASHTAG_RE.findall(f"{title} {body}")}
    upper_text = f" {title} {body} ".upper()
    detected.update(t for t in requested_tickers if re.search(rf"(?<![A-Z]){re.escape(t)}(?![A-Z])", upper_text))
    score, label = sentiment(f"{title} {body}")
    created = datetime.fromtimestamp(float(data.get("created_utc", 0)), timezone.utc).isoformat()
    return Post(
        id=str(data.get("id", "")), created_utc=created,
        subreddit=str(data.get("subreddit", "")), title=title, text=body,
        url=str(data.get("url", "")),
        permalink=f"https://www.reddit.com{data.get('permalink', '')}",
        score=int(data.get("score", 0)), comments=int(data.get("num_comments", 0)),
        tickers=sorted(detected), sentiment_score=score, sentiment_label=label,
    )


def collect(session: requests.Session, subreddits: list[str], query: str, tickers: set[str],
            limit: int, sort: str, time_filter: str) -> list[Post]:
    found: dict[str, Post] = {}
    for subreddit in subreddits:
        after = None
        while len(found) < limit:
            page_size = min(100, limit - len(found))
            params: dict[str, Any] = {
                "q": query, "restrict_sr": "on", "sort": sort,
                "t": time_filter, "limit": page_size, "raw_json": 1,
            }
            if after:
                params["after"] = after
            payload = api_get(session, f"/r/{subreddit}/search", params)
            listing = payload.get("data", {})
            children = listing.get("children", [])
            for child in children:
                post = parse_post(child.get("data", {}), tickers)
                if post.id:
                    found[post.id] = post
            after = listing.get("after")
            if not after or not children:
                break
        if len(found) >= limit:
            break
    return sorted(found.values(), key=lambda p: p.created_utc, reverse=True)[:limit]


def write_posts(posts: Iterable[Post], output: Path, fmt: str) -> None:
    rows = [asdict(post) for post in posts]
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with output.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        fields = list(Post.__dataclass_fields__)
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                row["tickers"] = ",".join(row["tickers"])
                writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="合规采集 Reddit 股票新闻及情绪数据")
    parser.add_argument("--subreddits", default="stocks,investing,wallstreetbets,StockMarket")
    parser.add_argument("--query", default="stock OR market OR earnings")
    parser.add_argument("--tickers", default="", help="逗号分隔，如 AAPL,TSLA,NVDA")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--sort", choices=("new", "hot", "top", "relevance", "comments"), default="new")
    parser.add_argument("--time", choices=("hour", "day", "week", "month", "year", "all"), default="day")
    parser.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    parser.add_argument("--output", type=Path, default=Path("data/reddit_market.jsonl"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit 必须在 1 到 1000 之间")

    user_agent = env("REDDIT_USER_AGENT")
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    token = get_token(session, env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET"))
    session.headers.update({"Authorization": f"Bearer {token}"})
    tickers = {x.strip().upper() for x in args.tickers.split(",") if x.strip()}
    posts = collect(session, [x.strip() for x in args.subreddits.split(",") if x.strip()],
                    args.query, tickers, args.limit, args.sort, args.time)
    write_posts(posts, args.output, args.format)
    print(f"已写入 {len(posts)} 条记录: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
