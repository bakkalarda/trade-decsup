"""CryptoPanic news connector — aggregated crypto news with sentiment labels.

CryptoPanic aggregates news from 50+ crypto news sources and provides:
  - Sentiment classification (bullish/bearish/neutral) via community votes
  - Filtering by coin (BTC, ETH, SOL, etc.)
  - Importance scoring (hot, important, lol, saved)
  - Free tier: 5 requests/minute, no auth token needed for basic access
  - With free auth token: 10 req/min, voting data, more filters

Set CRYPTOPANIC_API_KEY env var for enhanced access (free registration
at https://cryptopanic.com/developers/api/).

This feeds directly into the macro gate's headline_risk assessment,
replacing the current stub that delegates to the OpenClaw agent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

CRYPTOPANIC_BASE = "https://cryptopanic.com/api/free/v1"


class CryptoPanicConnector:
    """Fetch aggregated crypto news with sentiment from CryptoPanic.

    Provides headline risk assessment for the macro gate — the single
    biggest missing signal in the current system.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=15)
        self._api_key = os.environ.get("CRYPTOPANIC_API_KEY", "").strip()

    # ------------------------------------------------------------------
    # News feed
    # ------------------------------------------------------------------

    def fetch_news(
        self,
        currencies: str = "BTC",
        kind: str = "news",
        filter_: str = "hot",
        limit: int = 20,
    ) -> list[dict]:
        """Fetch latest news for a currency.

        Args:
            currencies: Comma-separated coin symbols (BTC, ETH, SOL)
            kind: 'news' or 'media' (social media posts)
            filter_: 'rising', 'hot', 'bullish', 'bearish', 'important', 'lol'
            limit: Max results (API max ~40 per page)

        Returns:
            List of news items with title, sentiment votes, source, timestamp.
        """
        params: dict = {
            "currencies": currencies,
            "kind": kind,
            "filter": filter_,
        }
        if self._api_key:
            params["auth_token"] = self._api_key

        try:
            resp = self._client.get(f"{CRYPTOPANIC_BASE}/posts/", params=params)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            if not results:
                return []

            items = []
            for post in results[:limit]:
                votes = post.get("votes", {})
                item = {
                    "title": post.get("title", ""),
                    "published_at": post.get("published_at", ""),
                    "source": post.get("source", {}).get("title", ""),
                    "url": post.get("url", ""),
                    "kind": post.get("kind", ""),
                    # Sentiment votes from CryptoPanic community
                    "positive_votes": votes.get("positive", 0),
                    "negative_votes": votes.get("negative", 0),
                    "important_votes": votes.get("important", 0),
                    "liked_votes": votes.get("liked", 0),
                    "lol_votes": votes.get("lol", 0),
                    # Currencies mentioned
                    "currencies": [
                        c.get("code", "") for c in post.get("currencies", [])
                    ],
                }

                # Derive sentiment
                pos = item["positive_votes"]
                neg = item["negative_votes"]
                total = pos + neg
                if total > 0:
                    item["sentiment_score"] = round((pos - neg) / total, 3)
                    item["sentiment"] = (
                        "bullish" if item["sentiment_score"] > 0.3
                        else "bearish" if item["sentiment_score"] < -0.3
                        else "neutral"
                    )
                else:
                    item["sentiment_score"] = 0.0
                    item["sentiment"] = "neutral"

                items.append(item)

            return items

        except Exception as e:
            logger.warning("CryptoPanic news fetch failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Sentiment aggregation
    # ------------------------------------------------------------------

    def fetch_headline_sentiment(
        self,
        currencies: str = "BTC",
    ) -> dict:
        """Aggregate headline sentiment across hot + important news.

        Returns a dict with:
          - headline_count: number of headlines analyzed
          - bullish_count / bearish_count / neutral_count
          - avg_sentiment_score: -1.0 (very bearish) to +1.0 (very bullish)
          - headline_risk: LOW / ELEVATED / HIGH
          - top_headlines: list of the most impactful headlines
          - bearish_dominance: True if bearish headlines dominate
        """
        result = {
            "headline_count": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "avg_sentiment_score": 0.0,
            "headline_risk": "LOW",
            "top_headlines": [],
            "bearish_dominance": False,
        }

        # Fetch from multiple filters for comprehensive coverage
        all_news: list[dict] = []

        for filter_type in ("hot", "important", "bullish", "bearish"):
            news = self.fetch_news(
                currencies=currencies,
                kind="news",
                filter_=filter_type,
                limit=15,
            )
            all_news.extend(news)

        if not all_news:
            return result

        # Deduplicate by title
        seen_titles: set[str] = set()
        unique_news: list[dict] = []
        for item in all_news:
            title = item["title"]
            if title not in seen_titles:
                seen_titles.add(title)
                unique_news.append(item)

        result["headline_count"] = len(unique_news)

        # Count sentiment
        sentiment_scores: list[float] = []
        for item in unique_news:
            sent = item.get("sentiment", "neutral")
            if sent == "bullish":
                result["bullish_count"] += 1
            elif sent == "bearish":
                result["bearish_count"] += 1
            else:
                result["neutral_count"] += 1
            sentiment_scores.append(item.get("sentiment_score", 0.0))

        if sentiment_scores:
            result["avg_sentiment_score"] = round(
                sum(sentiment_scores) / len(sentiment_scores), 3
            )

        # Determine headline risk
        bearish_pct = (
            result["bearish_count"] / result["headline_count"]
            if result["headline_count"] > 0
            else 0
        )
        important_bearish = sum(
            1 for item in unique_news
            if item.get("sentiment") == "bearish"
            and item.get("important_votes", 0) > 2
        )

        if bearish_pct > 0.5 or important_bearish >= 3:
            result["headline_risk"] = "HIGH"
            result["bearish_dominance"] = True
        elif bearish_pct > 0.3 or important_bearish >= 1:
            result["headline_risk"] = "ELEVATED"
            result["bearish_dominance"] = bearish_pct > 0.35
        else:
            result["headline_risk"] = "LOW"

        # Top headlines (sorted by importance + total votes)
        sorted_news = sorted(
            unique_news,
            key=lambda x: (
                x.get("important_votes", 0) * 3
                + abs(x.get("positive_votes", 0) - x.get("negative_votes", 0))
            ),
            reverse=True,
        )
        result["top_headlines"] = [
            {
                "title": n["title"],
                "sentiment": n["sentiment"],
                "source": n["source"],
            }
            for n in sorted_news[:5]
        ]

        return result

    # ------------------------------------------------------------------
    # Multi-asset sentiment (for regime classification)
    # ------------------------------------------------------------------

    def fetch_market_sentiment(self) -> dict:
        """Fetch aggregate sentiment across major cryptos.

        Useful for regime classification — if ALL coins have bearish
        headlines, it's a market-wide risk-off, not coin-specific.
        """
        result: dict = {"assets": {}}

        for coin in ("BTC", "ETH", "SOL"):
            sent = self.fetch_headline_sentiment(coin)
            result["assets"][coin] = {
                "headlines": sent["headline_count"],
                "avg_sentiment": sent["avg_sentiment_score"],
                "risk": sent["headline_risk"],
            }

        # Market-wide sentiment
        sentiments = [v["avg_sentiment"] for v in result["assets"].values()]
        if sentiments:
            result["market_avg_sentiment"] = round(
                sum(sentiments) / len(sentiments), 3
            )
            bearish_count = sum(1 for s in sentiments if s < -0.2)
            result["market_headline_risk"] = (
                "HIGH" if bearish_count >= 2
                else "ELEVATED" if bearish_count >= 1
                else "LOW"
            )
        else:
            result["market_avg_sentiment"] = 0.0
            result["market_headline_risk"] = "LOW"

        return result

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        try:
            news = self.fetch_news(currencies="BTC", limit=1)
            return {
                "status": "ok" if news else "error",
                "connector": "CryptoPanicConnector",
                "api_key_set": bool(self._api_key),
            }
        except Exception as e:
            return {
                "status": "error",
                "connector": "CryptoPanicConnector",
                "error": str(e),
            }

    def close(self):
        self._client.close()
