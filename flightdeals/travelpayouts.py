"""Client for the Travelpayouts / Aviasales Data API (free).

Prices come from Aviasales' cache of searches made by real users in the
last ~48h. That's why every deal is also given a Google Flights link to
double-check before booking.
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional
from urllib.parse import quote

import requests

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class ApiAuthError(RuntimeError):
    pass


@dataclass
class Fare:
    origin: str
    destination: str
    price: float
    depart: str                 # YYYY-MM-DD
    ret: Optional[str]          # YYYY-MM-DD (None = aller simple)
    airline: str = ""
    transfers: int = 0
    return_transfers: int = 0
    origin_airport: str = ""
    destination_airport: str = ""
    link: str = ""

    @property
    def stay_days(self) -> Optional[int]:
        if not self.ret:
            return None
        return (date.fromisoformat(self.ret) - date.fromisoformat(self.depart)).days

    @property
    def google_flights_url(self) -> str:
        o = self.origin_airport or self.origin
        d = self.destination_airport or self.destination
        q = f"Flights from {o} to {d} on {self.depart}"
        if self.ret:
            q += f" through {self.ret}"
        return "https://www.google.com/travel/flights?hl=fr&curr=EUR&q=" + quote(q)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.interval = 60.0 / max(per_minute, 1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


class TravelpayoutsClient:
    def __init__(self, token: str, currency: str = "eur", per_minute: int = 100, timeout: int = 30):
        self.currency = currency
        self.timeout = timeout
        self.limiter = RateLimiter(per_minute)
        self.session = requests.Session()
        self.session.headers.update({"X-Access-Token": token, "Accept-Encoding": "gzip, deflate"})

    def prices_for_dates(self, origin: str, destination: str, month: str,
                         one_way: bool = False, limit: int = 30) -> List[Fare]:
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": month,
            "currency": self.currency,
            "sorting": "price",
            "one_way": "true" if one_way else "false",
            "unique": "false",
            "limit": limit,
            "page": 1,
        }
        data = self._get(params)
        fares = []
        for t in data or []:
            fare = _parse_ticket(t, origin, destination)
            if fare is None:
                continue
            if not one_way and not fare.ret:
                continue
            fares.append(fare)
        return fares

    def _get(self, params: dict):
        last_err = None
        for attempt in range(4):
            self.limiter.wait()
            try:
                r = self.session.get(API_URL, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 403):
                raise ApiAuthError(f"Travelpayouts a refusé le token (HTTP {r.status_code}).")
            if r.status_code == 429 or r.status_code >= 500:
                last_err = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            if not body.get("success", True):
                raise RuntimeError(f"Erreur API : {body.get('error')}")
            return body.get("data")
        raise RuntimeError(f"Travelpayouts injoignable après plusieurs essais : {last_err}")


def _parse_ticket(t: dict, origin: str, destination: str) -> Optional[Fare]:
    try:
        price = float(t["price"])
        depart = str(t["departure_at"])[:10]
    except (KeyError, TypeError, ValueError):
        return None
    ret = t.get("return_at")
    link = t.get("link") or ""
    if link and link.startswith("/"):
        link = "https://www.aviasales.com" + link
    return Fare(
        origin=t.get("origin", origin),
        destination=t.get("destination", destination),
        price=price,
        depart=depart,
        ret=str(ret)[:10] if ret else None,
        airline=t.get("airline", "") or "",
        transfers=int(t.get("transfers") or 0),
        return_transfers=int(t.get("return_transfers") or 0),
        origin_airport=t.get("origin_airport", "") or "",
        destination_airport=t.get("destination_airport", "") or "",
        link=link,
    )


class MockClient:
    """Fake prices for local tests (--mock). Deterministic per run seed."""

    BASE = {"usa_est": 430, "usa_ouest": 540, "latam": 600, "asie_est": 680,
            "asie_se": 620, "oceanie": 1050}

    def __init__(self, regions_by_dest: dict, seed: int = 0):
        self.regions_by_dest = regions_by_dest
        self.seed = seed

    def prices_for_dates(self, origin, destination, month, one_way=False, limit=30):
        h = int(hashlib.md5(f"{origin}{destination}{month}{self.seed}".encode()).hexdigest(), 16)
        rnd = random.Random(h)
        y, m = map(int, month.split("-"))
        if one_way:  # positioning flight Marseille <-> hub
            return [Fare(origin, destination, rnd.randint(25, 90),
                         date(y, m, d).isoformat(), None, "VY", 0)
                    for d in range(1, 29) if rnd.random() < 0.7]
        base = self.BASE.get(self.regions_by_dest.get(destination, ""), 600)
        if origin == "MRS":
            base *= 1.25
        fares = []
        for _ in range(rnd.randint(0, 6)):
            d = date(y, m, rnd.randint(1, 28))
            stay = rnd.randint(4, 21)
            factor = rnd.choice([0.95, 1.0, 1.1, 1.2, 0.85]) if rnd.random() > 0.08 else rnd.uniform(0.45, 0.65)
            fares.append(Fare(origin, destination, round(base * factor), d.isoformat(),
                              (d + timedelta(days=stay)).isoformat(),
                              rnd.choice(["IB", "UX", "BA", "TP", "AF", "QR", "TK"]),
                              rnd.choice([0, 1, 1, 2]), rnd.choice([0, 1, 1]),
                              link="https://www.aviasales.com/"))
        return sorted(fares, key=lambda f: f.price)
