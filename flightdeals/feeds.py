"""Read flight-deal hunter websites (RSS) and keep relevant long-haul deals."""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import List

import feedparser
import requests

log = logging.getLogger(__name__)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def fetch_feed(source: dict) -> List[dict]:
    try:
        r = requests.get(source["url"], headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Flux %s indisponible : %s", source["name"], e)
        return []
    parsed = feedparser.parse(r.content)
    items = []
    for e in parsed.entries:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = datetime.fromtimestamp(calendar.timegm(e[key]), tz=timezone.utc)
                break
        items.append({
            "guid": e.get("id") or e.get("link") or e.get("title"),
            "source": source["name"],
            "title": (e.get("title") or "").strip(),
            "summary": (e.get("summary") or "")[:600],
            "link": e.get("link") or "",
            "published": published.isoformat() if published else "",
            "_published_dt": published,
        })
    return items


def is_relevant(item: dict, source: dict, filters: dict, city_names: List[str]) -> bool:
    if item["_published_dt"] and item["_published_dt"] < datetime.now(timezone.utc) - timedelta(days=filters["max_age_days"]):
        return False
    text = f" {item['title']} {item['summary']} ".lower()
    if any(k in text for k in filters["exclude_keywords"]):
        return False
    if source.get("require_origin", True) and not any(k in text for k in filters["origin_keywords"]):
        return False
    dest_words = [k.lower() for k in filters["destination_keywords"]] + [c.lower() for c in city_names]
    return any(k in text for k in dest_words)


def looks_like_error(item: dict, filters: dict) -> bool:
    text = f" {item['title']} {item.get('summary', '')} ".lower()
    return any(k.lower() in text for k in filters.get("error_keywords", []))
