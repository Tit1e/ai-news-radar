#!/usr/bin/env python3
"""Aggregate personal RSS subscriptions and produce snapshot data."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

UTC = timezone.utc
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
MOMOYU_RSS_URL = "https://momoyu.cc/api/hot/rss?code=MSw0NywyLDYsOTIsOSwzOCwyOSw0NSw4LDMyLDM2LDExLDgzLDQz"


@dataclass
class RawItem:
    site_id: str
    site_name: str
    source: str
    title: str
    url: str
    published_at: datetime | None


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = dtparser.parse(dt_str)
    except Exception:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def normalize_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url.strip())
        if not parsed.scheme:
            return raw_url.strip()
        query = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_"):
                continue
            if lk in {
                "ref",
                "spm",
                "fbclid",
                "gclid",
                "igshid",
                "mkt_tok",
                "mc_cid",
                "mc_eid",
                "_hsenc",
                "_hsmi",
            }:
                continue
            query.append((k, v))
        parsed = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(query, doseq=True),
        )
        return urlunparse(parsed).rstrip("/")
    except Exception:
        return raw_url.strip()


def host_of_url(raw_url: str) -> str:
    try:
        return urlparse(raw_url).netloc.lower()
    except Exception:
        return ""


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return ""


def parse_unix_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        n = float(value)
    except Exception:
        return None
    if n > 10_000_000_000:
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except Exception:
        return None


def parse_date_any(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if not value.tzinfo:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return parse_unix_timestamp(value)

    s = str(value).strip()
    if not s:
        return None

    if s.startswith("$D"):
        s = s[2:]

    if re.fullmatch(r"\d{12,}", s):
        return parse_unix_timestamp(int(s))
    if re.fullmatch(r"\d{9,11}", s):
        return parse_unix_timestamp(int(s))

    try:
        dt = dtparser.parse(s, tzinfos={"UT": 0, "UTC": 0, "GMT": 0})
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def parse_feed_entries_via_xml(feed_xml: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        root = ET.fromstring(feed_xml)
    except Exception:
        return out

    for tag in (".//item", ".//{*}item", ".//entry", ".//{*}entry"):
        for node in root.findall(tag):
            title = (node.findtext("title") or node.findtext("{*}title") or "").strip()
            link = ""
            link_node = node.find("link")
            if link_node is not None:
                link = (link_node.get("href") or link_node.text or "").strip()
            if not link:
                link = (node.findtext("{*}link") or node.findtext("link") or "").strip()
            published = (
                node.findtext("pubDate")
                or node.findtext("{*}pubDate")
                or node.findtext("published")
                or node.findtext("{*}published")
                or node.findtext("updated")
                or node.findtext("{*}updated")
            )
            if title and link:
                key = (title, link)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "link": link, "published": published})
    return out


def make_item_id(site_id: str, source: str, title: str, url: str) -> str:
    key = "||".join(
        [
            site_id.strip().lower(),
            source.strip().lower(),
            title.strip().lower(),
            normalize_url(url),
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def maybe_fix_mojibake(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    if re.search(r"[Ãâåèæïð]|[\x80-\x9f]|æ|ç|å|é", s) is None:
        return s
    for enc in ("latin1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if fixed and fixed != s:
                return fixed
        except Exception:
            continue
    return s


def normalize_source_for_display(site_id: str, source: str, url: str) -> str:
    src = (source or "").strip()
    if not src:
        host = host_of_url(url)
        if host.startswith("www."):
            host = host[4:]
        return host or "未分区"
    return src


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session


def parse_opml_subscriptions(opml_path: Path) -> list[dict[str, str]]:
    root = ET.parse(opml_path).getroot()
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for outline in root.findall(".//outline"):
        xml_url = str(outline.attrib.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        if xml_url in seen:
            continue
        seen.add(xml_url)
        title = first_non_empty(
            outline.attrib.get("title"),
            outline.attrib.get("text"),
            host_of_url(xml_url),
            xml_url,
        )
        html_url = str(outline.attrib.get("htmlUrl") or "").strip()
        out.append({"title": title, "xml_url": xml_url, "html_url": html_url})
    return out


def fetch_rss_feed(
    session: requests.Session,
    now: datetime,
    feed_url: str,
    site_id: str,
    site_name: str,
    feed_title: str,
    allow_missing_published: bool = False,
) -> tuple[list[RawItem], dict[str, Any]]:
    start = time.perf_counter()
    out: list[RawItem] = []
    error = None

    try:
        resp = session.get(feed_url, timeout=12)
        resp.raise_for_status()

        if feedparser is not None:
            parsed = feedparser.parse(resp.content)
            source_name = first_non_empty(
                feed_title,
                getattr(parsed, "feed", {}).get("title"),
                host_of_url(feed_url),
            )
            entries = parsed.entries
            for entry in entries:
                title = str(entry.get("title", "")).strip()
                link = str(entry.get("link", "")).strip()
                if not title or not link:
                    continue
                published = (
                    parse_date_any(entry.get("published"), now)
                    or parse_date_any(entry.get("updated"), now)
                    or parse_date_any(entry.get("pubDate"), now)
                )
                if not published and allow_missing_published:
                    published = now
                if not published:
                    continue
                out.append(
                    RawItem(
                        site_id=site_id,
                        site_name=site_name,
                        source=source_name,
                        title=title,
                        url=link,
                        published_at=published,
                    )
                )
        else:
            source_name = first_non_empty(feed_title, host_of_url(feed_url))
            entries = parse_feed_entries_via_xml(resp.content)
            for entry in entries:
                published = parse_date_any(entry.get("published"), now)
                if not published and allow_missing_published:
                    published = now
                if not published:
                    continue
                out.append(
                    RawItem(
                        site_id=site_id,
                        site_name=site_name,
                        source=source_name,
                        title=entry.get("title", ""),
                        url=entry.get("link", ""),
                        published_at=published,
                    )
                )
    except Exception as exc:
        error = str(exc)

    duration_ms = int((time.perf_counter() - start) * 1000)
    status = {
        "site_id": site_id,
        "site_name": site_name,
        "ok": error is None,
        "item_count": len(out),
        "duration_ms": duration_ms,
        "error": error,
        "feed_url": feed_url,
    }
    return out, status


def parse_momoyu_description_sections(description_html: str) -> list[dict[str, Any]]:
    html = (description_html or "").strip()
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    sections: list[dict[str, Any]] = []

    for h2 in soup.find_all("h2"):
        section_name = h2.get_text(" ", strip=True)
        entries: list[dict[str, Any]] = []

        cursor = h2.next_sibling
        while cursor is not None:
            tag_name = getattr(cursor, "name", None)
            if tag_name == "h2":
                break
            if tag_name == "p":
                a = cursor.find("a")
                text = cursor.get_text(" ", strip=True)
                url = (a.get("href") or "").strip() if a else ""
                rank = None
                m = re.match(r"^(\d+)\.\s*(.*)$", text)
                if m:
                    rank = int(m.group(1))
                    text = m.group(2).strip()
                entries.append({"rank": rank, "title": text, "url": url})
            cursor = cursor.next_sibling

        sections.append(
            {
                "section": section_name,
                "count": len(entries),
                "entries": entries,
            }
        )

    return sections


def fetch_momoyu_structured(session: requests.Session, feed_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "item_title": None,
        "pubDate": None,
        "section_count": 0,
        "sections": [],
    }
    try:
        resp = session.get(feed_url, timeout=12)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        item = root.find("./channel/item")
        if item is None:
            return out
        item_title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description_html = (item.findtext("description") or "").strip()
        sections = parse_momoyu_description_sections(description_html)
        return {
            "item_title": item_title,
            "pubDate": pub_date,
            "section_count": len(sections),
            "sections": sections,
        }
    except Exception:
        return out


def fetch_opml_rss(
    session: requests.Session,
    now: datetime,
    opml_path: Path,
    max_feeds: int = 0,
) -> tuple[list[RawItem], dict[str, Any], list[dict[str, Any]]]:
    feeds = parse_opml_subscriptions(opml_path)
    if max_feeds > 0:
        feeds = feeds[:max_feeds]

    out: list[RawItem] = []
    feed_statuses: list[dict[str, Any]] = []

    def fetch_one(feed: dict[str, str]) -> tuple[list[RawItem], dict[str, Any]]:
        feed_url = feed["xml_url"]
        items, status = fetch_rss_feed(
            session=session,
            now=now,
            feed_url=feed_url,
            site_id="opmlrss",
            site_name="OPML RSS",
            feed_title=feed["title"],
        )
        status["feed_title"] = feed["title"]
        status["effective_feed_url"] = feed_url
        status["skipped"] = False
        status["replaced"] = False
        return items, status

    if feeds:
        workers = min(20, max(4, len(feeds)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_one, f) for f in feeds]
            for future in as_completed(futures):
                items, status = future.result()
                out.extend(items)
                feed_statuses.append(status)

    feed_statuses.sort(key=lambda x: str(x.get("feed_title") or x.get("feed_url") or ""))
    total_duration_ms = sum(int(s.get("duration_ms") or 0) for s in feed_statuses)
    ok_feeds = sum(1 for s in feed_statuses if s["ok"])
    failed_feeds = sum(1 for s in feed_statuses if not s["ok"])

    summary_status = {
        "site_id": "opmlrss",
        "site_name": "OPML RSS",
        "ok": ok_feeds > 0,
        "partial_failures": failed_feeds,
        "item_count": len(out),
        "duration_ms": total_duration_ms,
        "error": None if failed_feeds == 0 else f"{failed_feeds} feeds failed",
        "feed_count": len(feeds),
        "effective_feed_count": len(feeds),
        "ok_feed_count": ok_feeds,
        "failed_feed_count": failed_feeds,
        "skipped_feed_count": 0,
        "replaced_feed_count": 0,
    }

    return out, summary_status, feed_statuses


def load_archive(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    items = payload.get("items", [])
    out: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for it in items:
            item_id = it.get("id")
            if item_id:
                out[item_id] = it
    elif isinstance(items, dict):
        for item_id, it in items.items():
            if isinstance(it, dict):
                it["id"] = item_id
                out[item_id] = it
    return out


def event_time(record: dict[str, Any]) -> datetime | None:
    return parse_iso(record.get("published_at")) or parse_iso(record.get("first_seen_at"))


def enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    title = maybe_fix_mojibake(str(out.get("title") or ""))
    out["title"] = title
    out["source"] = maybe_fix_mojibake(
        normalize_source_for_display(
            str(out.get("site_id") or ""),
            str(out.get("source") or ""),
            str(out.get("url") or ""),
        )
    )
    out["title_original"] = title
    out["title_en"] = None
    out["title_zh"] = None
    out["title_bilingual"] = title
    return out


def group_stats(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_site: dict[str, dict[str, Any]] = {}
    source_count = 0
    source_keys: set[str] = set()

    for item in items:
        sid = str(item.get("site_id") or "")
        source = str(item.get("source") or "")
        source_keys.add(f"{sid}::{source}")
        if sid not in by_site:
            by_site[sid] = {
                "site_id": sid,
                "site_name": str(item.get("site_name") or sid),
                "count": 0,
                "raw_count": 0,
            }
        by_site[sid]["count"] += 1
        by_site[sid]["raw_count"] += 1

    source_count = len(source_keys)
    site_stats = sorted(by_site.values(), key=lambda x: x["count"], reverse=True)
    return site_stats, source_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate personal subscription updates")
    parser.add_argument("--output-dir", default="data", help="Directory for output JSON files")
    parser.add_argument("--window-hours", type=int, default=24, help="Rolling window size in hours")
    parser.add_argument("--archive-days", type=int, default=7, help="Keep archive for N days")
    parser.add_argument("--rss-opml", default="", help="Optional OPML file path to include RSS sources")
    parser.add_argument("--rss-max-feeds", type=int, default=0, help="Optional max OPML RSS feeds to fetch (0 means all)")
    args = parser.parse_args()

    now = utc_now()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_path = output_dir / "archive.json"
    latest_path = output_dir / "latest-24h.json"
    status_path = output_dir / "source-status.json"
    title_cache_path = output_dir / "title-zh-cache.json"

    archive = load_archive(archive_path)
    session = create_session()

    raw_items: list[RawItem] = []
    statuses: list[dict[str, Any]] = []
    rss_feed_statuses: list[dict[str, Any]] = []

    if args.rss_opml:
        opml_path = Path(args.rss_opml).expanduser()
        if opml_path.exists():
            opml_items, opml_summary, rss_feed_statuses = fetch_opml_rss(
                session=session,
                now=now,
                opml_path=opml_path,
                max_feeds=max(0, int(args.rss_max_feeds)),
            )
            raw_items.extend(opml_items)
            statuses.append(opml_summary)
        else:
            statuses.append(
                {
                    "site_id": "opmlrss",
                    "site_name": "OPML RSS",
                    "ok": False,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": f"OPML not found: {opml_path}",
                    "feed_count": 0,
                    "ok_feed_count": 0,
                    "failed_feed_count": 0,
                }
            )

    momoyu_items, momoyu_status = fetch_rss_feed(
        session=session,
        now=now,
        feed_url=MOMOYU_RSS_URL,
        site_id="momoyurss",
        site_name="Momoyu RSS",
        feed_title="momoyu.cc",
        allow_missing_published=True,
    )
    raw_items.extend(momoyu_items)
    statuses.append(momoyu_status)
    momoyu_parsed = fetch_momoyu_structured(session, MOMOYU_RSS_URL)

    for raw in raw_items:
        title = raw.title.strip()
        url = normalize_url(raw.url)
        if not title or not url or not url.startswith("http"):
            continue
        item_id = make_item_id(raw.site_id, raw.source, title, url)

        existing = archive.get(item_id)
        if existing is None:
            archive[item_id] = {
                "id": item_id,
                "site_id": raw.site_id,
                "site_name": raw.site_name,
                "source": raw.source,
                "title": title,
                "url": url,
                "published_at": iso(raw.published_at),
                "first_seen_at": iso(now),
                "last_seen_at": iso(now),
            }
        else:
            existing["site_id"] = raw.site_id
            existing["site_name"] = raw.site_name
            existing["source"] = raw.source
            existing["title"] = title
            existing["url"] = url
            if raw.published_at:
                existing["published_at"] = iso(raw.published_at)
            existing["last_seen_at"] = iso(now)

    keep_after = now - timedelta(days=args.archive_days)
    pruned: dict[str, dict[str, Any]] = {}
    for item_id, record in archive.items():
        ts = (
            parse_iso(record.get("last_seen_at"))
            or parse_iso(record.get("published_at"))
            or parse_iso(record.get("first_seen_at"))
            or now
        )
        if ts >= keep_after:
            pruned[item_id] = record
    archive = pruned

    follow_items: list[dict[str, Any]] = []
    momoyu_items_out: list[dict[str, Any]] = []

    for record in archive.values():
        sid = str(record.get("site_id") or "")
        if sid not in {"opmlrss", "momoyurss"}:
            continue
        normalized = enrich_record(record)
        if sid == "opmlrss":
            follow_items.append(normalized)
        elif sid == "momoyurss":
            momoyu_items_out.append(normalized)

    follow_items.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    momoyu_items_out.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    follow_items = follow_items[:20]
    momoyu_items_out = momoyu_items_out[:20]

    subscription_items = sorted(
        follow_items + momoyu_items_out,
        key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    site_stats, source_count = group_stats(subscription_items)

    latest_payload = {
        "generated_at": iso(now),
        "window_hours": args.window_hours,
        "archive_total": len(archive),
        "site_count": len(site_stats),
        "source_count": source_count,
        "site_stats": site_stats,
        "total_items": len(subscription_items),
        "total_items_raw": len(subscription_items),
        "total_items_all_mode": len(subscription_items),
        "items": subscription_items,
        "items_ai": subscription_items,
        "items_all_raw": subscription_items,
        "items_all": subscription_items,
        "follow_opml_limit": 20,
        "follow_opml_count": len(follow_items),
        "follow_opml_items": follow_items,
        "momoyu_limit": 20,
        "momoyu_count": len(momoyu_items_out),
        "momoyu_items": momoyu_items_out,
        "momoyu_parsed": momoyu_parsed,
        "subscriptions": {
            "total_items": len(subscription_items),
            "items": subscription_items,
            "groups": site_stats,
            "sections": {
                "follow_opml": {
                    "limit": 20,
                    "count": len(follow_items),
                    "items": follow_items,
                },
                "momoyu": {
                    "limit": 20,
                    "count": len(momoyu_items_out),
                    "items": momoyu_items_out,
                    "parsed": momoyu_parsed,
                },
            },
        },
    }

    archive_payload = {
        "generated_at": iso(now),
        "total_items": len(archive),
        "items": sorted(
            archive.values(),
            key=lambda x: parse_iso(x.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        ),
    }

    status_payload = {
        "generated_at": iso(now),
        "sites": statuses,
        "successful_sites": sum(1 for s in statuses if s.get("ok")),
        "failed_sites": [s.get("site_id") for s in statuses if not s.get("ok")],
        "zero_item_sites": [
            s.get("site_id") for s in statuses if s.get("ok") and int(s.get("item_count") or 0) == 0
        ],
        "fetched_raw_items": len(raw_items),
        "items_before_filter": len(subscription_items),
        "items_in_window": len(subscription_items),
        "rss_opml": {
            "enabled": bool(args.rss_opml),
            "path": str(Path(args.rss_opml).expanduser()) if args.rss_opml else None,
            "feed_total": len(rss_feed_statuses),
            "effective_feed_total": len(rss_feed_statuses),
            "ok_feeds": sum(1 for s in rss_feed_statuses if s.get("ok")),
            "failed_feeds": [s.get("effective_feed_url") or s.get("feed_url") for s in rss_feed_statuses if not s.get("ok")],
            "zero_item_feeds": [
                s.get("effective_feed_url") or s.get("feed_url")
                for s in rss_feed_statuses
                if s.get("ok") and int(s.get("item_count") or 0) == 0
            ],
            "skipped_feeds": [],
            "replaced_feeds": [],
            "feeds": rss_feed_statuses,
        },
    }

    latest_path.write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    title_cache_path.write_text(json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote: {latest_path} ({len(subscription_items)} items)")
    print(f"Wrote: {archive_path} ({len(archive)} items)")
    print(f"Wrote: {status_path}")
    print(f"Wrote: {title_cache_path} (0 entries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
