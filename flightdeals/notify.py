"""Telegram alerts."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fr_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    label = f"{d.day} {MOIS[d.month - 1]}"
    return label if d.year == date.today().year else f"{label} {d.year}"


def eur(x: Optional[float]) -> str:
    return "?" if x is None else f"{round(x):,} €".replace(",", " ")


def stops(n: int) -> str:
    return "direct" if n == 0 else f"{n} escale" + ("s" if n > 1 else "")


def now_label() -> str:
    n = datetime.now(PARIS)
    return f"{n.day} {MOIS[n.month - 1]} · {n.hour}h{n.minute:02d}"


# ---------------------------------------------------------------- Telegram
def deal_block_telegram(d: dict, cfg: dict) -> str:
    f = d["fare"]
    o_name = cfg["origins"][f.origin]["name"]
    dest = cfg["destinations"][f.destination]
    stay = f.stay_days
    n = cfg.get("travelers", 1)
    lines = [
        f"{dest.get('flag', '✈️')} <b>{html.escape(o_name)} → {html.escape(dest['name'])}</b> · <b>{eur(f.price)}</b>/pers A/R",
        f"📅 {fr_date(f.depart)} → {fr_date(f.ret)} ({stay} j) · {stops(max(f.transfers, f.return_transfers))}"
        + (f" · {html.escape(f.airline)}" if f.airline else ""),
    ]
    if d.get("usual"):
        drop = 1 - f.price / d["usual"]
        if drop > 0.02:
            lines.append(f"📉 −{drop:.0%} vs prix habituel ({eur(d['usual'])})")
    total = d.get("home_total") or f.price
    if f.origin != cfg["home"]:
        approx = " (estimation)" if d.get("pos_estimated") else ""
        detail = (f" (+{eur(d['pos_out'])} aller, +{eur(d['pos_back'])} retour{approx})"
                  if d.get("pos_out") is not None else approx)
        lines.append(f"🏠 Depuis Marseille : ~{eur(total)}/pers{detail}")
    if n > 1:
        lines.append(f"👥 <b>~{eur(total * n)} pour {n}</b> tout compris depuis Marseille")
    if d.get("other_months"):
        lines.append(f"➕ {d['other_months']} autre(s) mois aussi en promo sur cette route")
    links = []
    if f.link:
        links.append(f'<a href="{html.escape(f.link)}">Réserver</a>')
    links.append(f'<a href="{html.escape(f.google_flights_url)}">Vérifier sur Google Flights</a>')
    lines.append(" · ".join(links))
    return "\n".join(lines)


def build_telegram(deals: List[dict], feed_items: List[dict], cfg: dict, warning: str = "") -> str:
    parts = [f"✈️ <b>Chasseur de vols</b> — {now_label()}"]
    if warning:
        parts.append(f"⚠️ {html.escape(warning)}")
    exc = [d for d in deals if d["exceptional"]]
    normal = [d for d in deals if not d["exceptional"]]
    if exc:
        parts.append("🚨 <b>PRIX EXCEPTIONNELS</b>")
        parts += [deal_block_telegram(d, cfg) for d in exc]
    if normal:
        parts.append("💸 <b>Bons plans</b>")
        parts += [deal_block_telegram(d, cfg) for d in normal]
    errs = [i for i in feed_items if i.get("error")]
    others = [i for i in feed_items if not i.get("error")]
    if errs:
        parts.insert(2 if warning else 1, "🚨 <b>ERREUR DE PRIX / FLASH — réserve vite, ça part en quelques heures</b>\n"
                     + "\n".join(f"• <a href=\"{html.escape(i['link'])}\">{html.escape(i['title'])}</a> "
                                  f"<i>({html.escape(i['source'])})</i>" for i in errs))
    if others:
        parts.append("🔥 <b>Repérés par les chasseurs de deals</b>\n"
                     + "\n".join(f"• <a href=\"{html.escape(i['link'])}\">{html.escape(i['title'])}</a> "
                                  f"<i>({html.escape(i['source'])})</i>" for i in others))
    return "\n\n".join(parts)


def send_telegram(text: str, token: str, chat_id: str):
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800 and cur:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
            "chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True})
        if not r.ok:
            raise RuntimeError(f"Telegram a refusé le message : {r.status_code} {r.text[:200]}")
