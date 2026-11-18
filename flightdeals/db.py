"""SQLite storage: price history, detected deals, alerts already sent, feed items seen."""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    run_at TEXT, origin TEXT, destination TEXT, month TEXT, price REAL
);
CREATE INDEX IF NOT EXISTS idx_obs ON observations(origin, destination, month, run_at);
CREATE TABLE IF NOT EXISTS deals (
    run_at TEXT, origin TEXT, destination TEXT, depart TEXT, ret TEXT, price REAL,
    airline TEXT, transfers INTEGER, return_transfers INTEGER, origin_airport TEXT,
    destination_airport TEXT, link TEXT, gflights TEXT, usual REAL, exceptional INTEGER,
    reason TEXT, home_total REAL, positioning_out REAL, positioning_back REAL, positioning_estimated INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deals ON deals(run_at);
CREATE TABLE IF NOT EXISTS sent_alerts (key TEXT PRIMARY KEY, price REAL, sent_at TEXT);
CREATE TABLE IF NOT EXISTS feed_items (
    guid TEXT PRIMARY KEY, source TEXT, title TEXT, link TEXT, published TEXT,
    seen_at TEXT, matched INTEGER
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- price history -------------------------------------------------
    def add_observation(self, run_at: str, origin: str, dest: str, month: str, price: float):
        self.conn.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (run_at, origin, dest, month, price))

    def usual_price(self, origin: str, dest: str, month: str, days: int, min_n: int) -> Optional[float]:
        """Median of past cheapest prices: same month if enough data, else whole route."""
        since = (utcnow() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT price FROM observations WHERE origin=? AND destination=? AND month=? AND run_at>=?",
            (origin, dest, month, since)).fetchall()
        if len(rows) >= min_n:
            return statistics.median(r[0] for r in rows)
        rows = self.conn.execute(
            "SELECT price FROM observations WHERE origin=? AND destination=? AND run_at>=?",
            (origin, dest, since)).fetchall()
        if len(rows) >= min_n * 2:
            return statistics.median(r[0] for r in rows)
        return None

    # --- deals ---------------------------------------------------------
    def add_deal(self, run_at: str, d: dict):
        f = d["fare"]
        self.conn.execute(
            "INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_at, f.origin, f.destination, f.depart, f.ret, f.price, f.airline, f.transfers,
             f.return_transfers, f.origin_airport, f.destination_airport, f.link,
             f.google_flights_url, d.get("usual"), int(d["exceptional"]), d["reason"],
             d.get("home_total"), d.get("pos_out"), d.get("pos_back"), int(d.get("pos_estimated", False))))

    def deals_since(self, hours: int):
        since = (utcnow() - timedelta(hours=hours)).isoformat()
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM deals WHERE run_at>=? ORDER BY price", (since,)).fetchall()]

    # --- alerts dedup --------------------------------------------------
    def should_alert(self, key: str, price: float, resend_days: int, cheaper_by: float) -> bool:
        row = self.conn.execute("SELECT price, sent_at FROM sent_alerts WHERE key=?", (key,)).fetchone()
        if row is None:
            return True
        sent_at = datetime.fromisoformat(row["sent_at"])
        if utcnow() - sent_at > timedelta(days=resend_days):
            return True
        return price <= row["price"] * (1 - cheaper_by)

    def mark_alerted(self, key: str, price: float):
        self.conn.execute("INSERT OR REPLACE INTO sent_alerts VALUES (?,?,?)", (key, price, utcnow().isoformat()))

    # --- feeds ---------------------------------------------------------
    def feed_seen(self, guid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM feed_items WHERE guid=?", (guid,)).fetchone() is not None

    def add_feed_item(self, item: dict, matched: bool):
        self.conn.execute("INSERT OR IGNORE INTO feed_items VALUES (?,?,?,?,?,?,?)",
                          (item["guid"], item["source"], item["title"], item["link"],
                           item.get("published", ""), utcnow().isoformat(), int(matched)))

    def feed_matches_since(self, hours: int):
        since = (utcnow() - timedelta(hours=hours)).isoformat()
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM feed_items WHERE matched=1 AND seen_at>=? ORDER BY seen_at DESC", (since,)).fetchall()]

    # --- housekeeping --------------------------------------------------
    def prune(self, keep_days: int = 150):
        since = (utcnow() - timedelta(days=keep_days)).isoformat()
        self.conn.execute("DELETE FROM observations WHERE run_at<?", (since,))
        self.conn.execute("DELETE FROM deals WHERE run_at<?", (since,))
        self.conn.execute("DELETE FROM feed_items WHERE seen_at<?", (since,))

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.execute("VACUUM")
        self.conn.close()
