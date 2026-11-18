"""Entry point: scan prices, detect deals, send Telegram alerts as soon as something is found.

    python -m flightdeals.main --slot auto     # scanne une ville de départ (tourne chaque heure) + sites de deals
    python -m flightdeals.main --feeds-only    # sites de deals uniquement (toutes les 10 min)
    python -m flightdeals.main --mock --dry-run  # test local sans clés (toutes les villes)
"""
from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import feeds as feedmod
from .db import DB, utcnow
from .notify import build_telegram, send_telegram
from .travelpayouts import ApiAuthError, Fare, MockClient, TravelpayoutsClient

log = logging.getLogger("flightdeals")
ROOT = Path(__file__).resolve().parent.parent


def months_ahead(n: int) -> List[str]:
    d = date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    return out


# ------------------------------------------------------------------ scan
def scan(client, cfg: dict, origins: List[str], months: List[str]) -> Tuple[Dict[tuple, List[Fare]], int, int]:
    tasks = [(o, d, m) for o in origins for d in cfg["destinations"] for m in months]
    results: Dict[tuple, List[Fare]] = {}
    errors = 0
    lo, hi = cfg["min_stay_days"], cfg["max_stay_days"]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(client.prices_for_dates, o, d, m): (o, d, m) for o, d, m in tasks}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                fares = fut.result()
            except ApiAuthError:
                ex.shutdown(cancel_futures=True)
                raise
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.debug("échec %s : %s", key, e)
                continue
            results[key] = [f for f in fares if f.stay_days is not None and lo <= f.stay_days <= hi]
    return results, errors, len(tasks)


def positioning_maps(client, cfg: dict, origins: List[str], months: List[str]) -> Dict[str, Tuple[dict, dict]]:
    """For each hub (Madrid, Londres): cheapest one-way per day home→hub and hub→home."""
    maps = {}
    for hub in origins:
        home = cfg["origins"][hub].get("positioning_from")
        if not home:
            continue
        out_map, back_map = {}, {}
        for m in months:
            for src, dst, target in ((home, hub, out_map), (hub, home, back_map)):
                try:
                    for f in client.prices_for_dates(src, dst, m, one_way=True, limit=1000):
                        if f.depart not in target or f.price < target[f.depart]:
                            target[f.depart] = f.price
                except ApiAuthError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.debug("positionnement %s→%s %s : %s", src, dst, m, e)
        maps[hub] = (out_map, back_map)
    return maps


def _pos_cost(daymap: dict, days: List[str], ground: Optional[float]) -> Tuple[Optional[float], bool]:
    vals = [daymap[d] for d in days if d in daymap]
    if vals:
        best, est = min(vals), False
    elif daymap:
        best, est = statistics.median(daymap.values()), True
    else:
        best, est = None, True
    if ground is not None and (best is None or ground <= best):
        return float(ground), False
    return best, est


def home_cost(fare: Fare, cfg: dict, pos: dict) -> dict:
    if fare.origin == cfg["home"] or fare.origin not in pos:
        return {"home_total": fare.price}
    out_map, back_map = pos[fare.origin]
    ground = cfg["origins"][fare.origin].get("ground_one_way")
    dep, ret = date.fromisoformat(fare.depart), date.fromisoformat(fare.ret)
    # arriver la veille ou le jour même ; repartir le jour même ou le lendemain
    out, est1 = _pos_cost(out_map, [(dep - timedelta(days=1)).isoformat(), dep.isoformat()], ground)
    back, est2 = _pos_cost(back_map, [ret.isoformat(), (ret + timedelta(days=1)).isoformat()], ground)
    if out is None or back is None:
        return {"home_total": None}
    return {"home_total": fare.price + out + back, "pos_out": out, "pos_back": back,
            "pos_estimated": est1 or est2}


# ------------------------------------------------------------------ deals
def detect(results, cfg: dict, db: DB, pos: dict, run_at: str) -> List[dict]:
    a = cfg["alerts"]
    deals = []
    for (o, d, m), fares in results.items():
        if not fares:
            continue
        region = cfg["regions"][cfg["destinations"][d]["region"]]
        # meilleur billet = le moins cher en coût total depuis Marseille (parmi les 5 moins chers)
        cands = []
        for f in sorted(fares, key=lambda f: f.price)[:5]:
            hc = home_cost(f, cfg, pos)
            total = hc.get("home_total")
            if total is None:  # trajet de rapprochement inconnu → estimation prudente
                total = f.price + 2 * (cfg["origins"][o].get("ground_one_way") or 60)
                hc = {"home_total": total, "pos_estimated": True}
            cands.append((total, f, hc))
        total, best, hc = min(cands, key=lambda c: c[0])
        usual = db.usual_price(o, d, m, a["history_days"], a["min_history"])
        reasons, exceptional = [], False
        if total <= region["exceptional"]:
            exceptional = True
            reasons.append("sous le seuil imbattable")
        elif total <= region["deal"]:
            reasons.append("sous le seuil")
        if usual:
            drop = 1 - best.price / usual
            if drop >= a["exceptional_drop"]:
                exceptional = True
            if drop >= a["drop_vs_usual"]:
                reasons.append(f"-{drop:.0%} vs habituel")
        if reasons:
            deal = {"fare": best, "usual": usual, "exceptional": exceptional,
                    "reason": ", ".join(reasons), "month": m}
            deal.update(hc)
            deals.append(deal)
    # record history AFTER computing the usual price (no self-comparison)
    for (o, d, m), fares in results.items():
        if fares:
            db.add_observation(run_at, o, d, m, min(f.price for f in fares))
    for deal in deals:
        db.add_deal(run_at, deal)
    return deals


def _rank(d: dict, cfg: dict) -> tuple:
    region = cfg["regions"][cfg["destinations"][d["fare"].destination]["region"]]
    ratio = (d.get("home_total") or d["fare"].price) / region["deal"]
    if d.get("usual"):
        ratio = min(ratio, d["fare"].price / d["usual"] + 0.3)
    return (not d["exceptional"], ratio)


def pick_alerts(deals: List[dict], cfg: dict, db: DB) -> List[dict]:
    """One line per route (its best month), only if new or cheaper than last alert."""
    a = cfg["alerts"]
    by_route: Dict[tuple, List[dict]] = {}
    for d in deals:
        by_route.setdefault((d["fare"].origin, d["fare"].destination), []).append(d)
    chosen = []
    for route, ds in by_route.items():
        ds.sort(key=lambda d: (not d["exceptional"], d.get("home_total") or d["fare"].price))
        fresh = [d for d in ds if db.should_alert(f"{route[0]}-{route[1]}-{d['month']}", d["fare"].price,
                                                  a["resend_after_days"], a["resend_if_cheaper_by"])]
        if fresh:
            top = dict(fresh[0])
            top["other_months"] = len(fresh) - 1
            top["_keys"] = [(f"{route[0]}-{route[1]}-{d['month']}", d["fare"].price) for d in fresh]
            chosen.append(top)
    chosen.sort(key=lambda d: _rank(d, cfg))
    return chosen[: a["telegram_max_routes"]]


# ------------------------------------------------------------------ feeds
def check_feeds(cfg: dict, db: DB, offline: bool) -> List[dict]:
    if offline:
        return [{"guid": "mock-1", "source": "Fly4free", "title": "🔥 ERROR FARE: Madrid to Tokyo from €289 return",
                 "link": "https://www.fly4free.com/", "published": "", "error": True}]
    names = [v["name"] for v in cfg["destinations"].values()]
    new = []
    for src in cfg["feeds"]:
        for it in feedmod.fetch_feed(src):
            if db.feed_seen(it["guid"]):
                continue
            ok = feedmod.is_relevant(it, src, cfg["feed_filters"], names)
            db.add_feed_item(it, ok)
            if ok:
                it["error"] = feedmod.looks_like_error(it, cfg["feed_filters"])
                new.append(it)
    new.sort(key=lambda i: not i.get("error"))
    return new


# ------------------------------------------------------------------ main
def pick_origins(cfg: dict, slot: Optional[str]) -> List[str]:
    """--slot auto : une ville de départ par heure (rotation) ; --slot N : la N-ième ; rien : toutes."""
    origins = list(cfg["origins"])
    if slot is None:
        return origins
    n = utcnow().hour if slot == "auto" else int(slot)
    return [origins[n % len(origins)]]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--slot", default=None, help="'auto' = une ville de départ par heure ; N = la N-ième ; absent = toutes")
    p.add_argument("--feeds-only", action="store_true", help="ne vérifie que les sites de deals (rapide)")
    p.add_argument("--no-feeds", action="store_true", help="ne vérifie pas les sites de deals")
    p.add_argument("--mock", action="store_true", help="prix simulés (test sans clé API)")
    p.add_argument("--dry-run", action="store_true", help="n'envoie rien : affiche le message Telegram")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--db", default=str(ROOT / "data" / "deals.db"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    db = DB(args.db)
    run_at = utcnow().isoformat()
    months = months_ahead(cfg["months_ahead"])
    origins = pick_origins(cfg, args.slot)

    if args.mock:
        client = MockClient({k: v["region"] for k, v in cfg["destinations"].items()}, seed=int(run_at[11:13]))
    else:
        token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
        if not token and not args.feeds_only:
            log.warning("TRAVELPAYOUTS_TOKEN pas encore configuré : scanner de prix en pause.")
            db.close()
            return 0
        client = TravelpayoutsClient(token, cfg["currency"], cfg["requests_per_minute"])

    warning = ""
    results, errors, total, pos = {}, 0, 0, {}
    if not args.feeds_only:
        log.info("Départs scannés : %s", ", ".join(cfg["origins"][o]["name"] for o in origins))
        try:
            results, errors, total = scan(client, cfg, origins, months)
            pos = positioning_maps(client, cfg, origins, months)
        except ApiAuthError as e:
            results, errors, total, pos = {}, 1, 1, {}
            warning = f"{e} Vérifie le secret TRAVELPAYOUTS_TOKEN."
        log.info("Scan : %d requêtes, %d erreurs, %d routes-mois avec prix", total, errors,
                 sum(1 for v in results.values() if v))
        if not warning and total and errors > total * 0.5:
            warning = f"Scan dégradé : {errors}/{total} requêtes ont échoué."

    deals = detect(results, cfg, db, pos, run_at)
    alerts = pick_alerts(deals, cfg, db)
    feed_new = [] if args.no_feeds else check_feeds(cfg, db, offline=args.mock)
    log.info("%d deals détectés, %d routes à signaler, %d deals repérés sur les sites", len(deals), len(alerts), len(feed_new))

    if alerts or feed_new or warning:
        text = build_telegram(alerts, feed_new, cfg, warning)
        if args.dry_run:
            out_dir = ROOT / "out"
            out_dir.mkdir(exist_ok=True)
            (out_dir / "telegram.txt").write_text(text, encoding="utf-8")
            print("\n===== TELEGRAM =====\n" + text)
        else:
            send_telegram(text, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
        for d in alerts:
            for key, price in d["_keys"]:
                db.mark_alerted(key, price)

    db.prune()
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
