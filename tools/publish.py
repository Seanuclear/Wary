#!/usr/bin/env python3
"""
Public edition feed builder. Writes site/feed.json. Standard library only.

What goes into the public feed:
  * EDITORIAL: your baseline levels and curated signals (editorial/*.json), written in your own words.
  * OFFICIAL, automatic: the MI5 threat level, GOV.UK and NCSC headlines, Met Office weather warnings.
    All of these are Crown copyright and open-licensed.
  * Press feeds (BBC, Guardian, Sky and so on) are NOT fetched here. Their terms allow personal use, not
    republication. Use the private "desk" edition for your own reading, then write signals yourself.

Ratings in the public edition change only when the editor changes them, apart from the MI5 threat level,
which sets a floor for the terrorism area.

    python3 tools/publish.py                       # fetch and write site/feed.json
    python3 tools/publish.py --fixtures DIR        # offline test run
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import defusedxml.ElementTree as ET  # type: ignore
except Exception:  # pragma: no cover
    import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "HouseholdThreatWatch-public/1.0 (non-commercial; contact via site)"
AREAS = ["energy", "cyber", "comms", "security", "military", "supply"]
AREA_NAMES = {"energy": "Power, gas and fuel", "cyber": "Cyber attacks on services", "comms": "Cables, GPS and phone networks",
              "security": "Terrorism and sabotage", "military": "Military and nuclear escalation", "supply": "Food, water and supply chains"}
LEVEL_NAMES = {1: "Routine", 2: "Aware", 3: "Elevated", 4: "High", 5: "Critical"}
RX = {
    "energy": r"power cut|blackout|national grid|\bneso\b|electricity (supply|grid|network)|gas (supply|network|shortage)|national gas|energy (supply|security|infrastructure)|fuel (shortage|supply)|pipeline|interconnector|substation|power station|\blng\b",
    "cyber": r"cyber|ransomware|malware|hackers?|hacking|ddos|data breach|\bncsc\b|\bgchq\b|phishing",
    "comms": r"undersea|subsea|seabed|submarine cable|fibre|telecoms?|mobile network|broadband|\bgps\b|jamming|satellite|\bofcom\b",
    "security": r"terror|threat level|\bjtac\b|\bmi5\b|counter[- ]terror|sabotage|arson|extremis[tm]|espionage|state[- ]linked|hostile state|explosive",
    "military": r"\bnato\b|nuclear|warhead|missile|russian navy|russian submarine|escalat|airspace|ministry of defence|\btrident\b|strategic defence",
    "supply": r"food (shortage|supply)|supply chain|water (supply|company|utility)|resilience|emergency (preparedness|planning)|stockpil",
}
RX = {k: re.compile(v, re.I) for k, v in RX.items()}
CALM = re.compile(r"consultation|appoint|obituary|award|honour|anniversary|vacanc|job\b|recruit|procurement|contract notice", re.I)


def now_utc():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    for f in (lambda: parsedate_to_datetime(s), lambda: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            d = f()
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def clean(s, limit=0):
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[: limit - 1].rsplit(" ", 1)[0] + "…" if limit and len(s) > limit else s


def safe_url(u):
    return u if urlparse((u or "").strip()).scheme in ("http", "https") else ""


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(el, *names):
    for c in list(el):
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def fetch(url, source_id, fixtures, headers=None):
    if fixtures:
        for ext in ("xml", "atom", "html", "json"):
            p = os.path.join(fixtures, f"{source_id}.{ext}")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    return f.read()
        raise FileNotFoundError(f"no fixture for {source_id}")
    with urlopen(Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})}), timeout=20) as r:
        return r.read(3_000_000)


def parse_feed(data):
    out = []
    for el in ET.fromstring(data).iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        link = ""
        for c in list(el):
            if local(c.tag) == "link":
                link = c.attrib.get("href") or (c.text or "").strip()
                if link:
                    break
        title = clean(child_text(el, "title"))
        if title:
            out.append({"title": title, "url": safe_url(link), "summary": clean(child_text(el, "description", "summary", "content"), 300),
                        "published": parse_dt(child_text(el, "pubdate", "published", "updated", "date"))})
    return out


TERROR_ORDER = ["LOW", "MODERATE", "SUBSTANTIAL", "SEVERE", "CRITICAL"]
LEVEL_WORDS = "LOW|MODERATE|SUBSTANTIAL|SEVERE|CRITICAL"


def read_terror_level(data):
    """Returns (level, contextual). 'contextual' means the word sat right after 'national threat level'.
    A loose match (first level word on the page) is only ever used to CONFIRM the last known level."""
    text = clean(data.decode("utf-8", "replace"))
    m = re.search(r"(?i:national (?:terrorism )?threat level)[^.]{0,120}?\b(" + LEVEL_WORDS + r")\b", text)
    if m:
        return m.group(1), True
    for m in re.finditer(r"\b(" + LEVEL_WORDS + r")\b", text):
        if "northern ireland" in text[max(0, m.start() - 140): m.start()].lower():
            continue
        return m.group(1), False
    raise ValueError("no national threat level found on the page")


def accept_terror(found, contextual, last_known, state=None, now=None, hold_hours=6):
    """Returns (accepted, pending_key). A page redesign must never be able to invent a false level.
    - A loose match (not tied to 'national threat level') can only CONFIRM the last accepted level.
    - A clear statement within one step of the last accepted level is accepted.
    - A bigger jump is accepted only after the same reading has held for the hold time (pending_key tracks that)."""
    if not last_known or last_known not in TERROR_ORDER:
        return contextual, None
    if not contextual:
        return found == last_known, None
    if abs(TERROR_ORDER.index(found) - TERROR_ORDER.index(last_known)) <= 1:
        return True, None
    key = f"terror-jump:{found}"
    return (bool(now) and held(state, key, now, hold_hours)), key


ALERTS_URL = "https://www.gov.uk/alerts"
GRID_URL = "https://data.elexon.co.uk/bmrs/api/v1/system/warnings?format=json"
GAS_URL = "https://www.nationalgas.com/balancing/margins-notices-and-gas-deficit-warnings"
TERROR_TEXT = {"LOW": "An attack is unlikely.", "MODERATE": "An attack is possible but not likely.", "SUBSTANTIAL": "An attack is likely.",
               "SEVERE": "An attack is highly likely.", "CRITICAL": "An attack is expected imminently."}


def check_alerts(fixtures, now):
    """Says 'None current' ONLY if the page positively says so. Anything else means: go and look."""
    base = {"id": "alerts", "name": "Emergency Alerts", "url": ALERTS_URL, "source": "GOV.UK", "checked": iso(now)}
    try:
        text = clean(fetch(ALERTS_URL, "alerts", fixtures).decode("utf-8", "replace"))
        if re.search(r"no current alerts", text, re.I):
            return dict(base, state="clear", text="None current", detail="Sent by the emergency services and government. None are live.")
        return dict(base, state="unknown", text="Could not confirm", detail="Check GOV.UK now. This page could not read the current alerts.")
    except Exception as e:
        print(f"warning: alerts check: {e}", file=sys.stderr)
        return dict(base, state="unknown", text="Could not check", detail="Check GOV.UK now.")


def check_grid(fixtures, now):
    base = {"id": "grid", "name": "Electricity grid notices", "url": "https://bmrs.elexon.co.uk/", "source": "Elexon BMRS", "checked": iso(now),
            "extra": {"text": "Gas notices: National Gas", "url": GAS_URL}}
    try:
        j = json.loads(fetch(GRID_URL, "grid", fixtures).decode("utf-8", "replace"))
        rows = j.get("data", j) if isinstance(j, dict) else j
        recent, unread = [], 0
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            when = parse_dt(str(r.get("publishTime") or r.get("publishDateTime") or ""))
            text = clean(str(r.get("warningText") or r.get("warning") or r.get("text") or ""))
            if when is None:
                unread += 1
            elif now - when <= timedelta(hours=24):
                recent.append(text)
        if recent:
            if any(re.search(r"demand control|load shedding|blackout|national electricity transmission system emergency", t, re.I) for t in recent):
                return dict(base, state="alert", text="Emergency measures mentioned", detail="Check National Energy System Operator updates now.")
            return dict(base, state="notice", text="Market notice issued", detail="These are routine most winters and do not mean supplies are at risk.")
        if unread:
            return dict(base, state="unknown", text="Could not read notice times", detail="Check the source directly.")
        return dict(base, state="clear", text="No notices in 24 hours", detail="Electricity margins look normal.")
    except Exception as e:
        print(f"warning: grid check: {e}", file=sys.stderr)
        return dict(base, state="unknown", text="Could not check", detail="Check the source directly.")


SPACE_URL = "https://services.swpc.noaa.gov/products/noaa-scales.json"
MET_SPACE_URL = "https://weather.metoffice.gov.uk/specialist-forecasts/space-weather"
G_WORDS = {3: "Strong", 4: "Severe", 5: "Extreme"}


def check_space(fixtures, now):
    """Space weather (geomagnetic storms) is a natural hazard. It is shown so a GPS or grid problem is not
    mistaken for an attack. It never changes a level. Only 'Quiet to moderate' if a value was positively read."""
    base = {"id": "space", "name": "Space weather", "url": MET_SPACE_URL, "source": "Met Office", "checked": iso(now),
            "extra": {"text": "Levels from NOAA", "url": "https://www.swpc.noaa.gov/"}}
    try:
        j = json.loads(fetch(SPACE_URL, "space", fixtures).decode("utf-8", "replace"))
        seen = []
        for k in ("-1", "0", "1"):  # latest observed, 24 hour maximum, next day forecast
            g = ((j.get(k) or {}).get("G") or {}).get("Scale")
            try:
                seen.append(int(str(g).strip()))
            except (TypeError, ValueError):
                pass
        if not seen:
            return dict(base, state="unknown", text="Could not read", detail="Check the Met Office space weather forecast.")
        top = max(seen)
        if top >= 5:
            return dict(base, state="alert", text="Extreme storm (G5)", detail="Storms this size can disturb GPS, radio and the power grid. Check the Met Office before assuming a hostile cause.")
        if top >= 3:
            return dict(base, state="notice", text=f"{G_WORDS[top]} storm (G{top})", detail="Expected or under way. It can disturb GPS and radio, and rarely the power grid. Check the Met Office before assuming a hostile cause.")
        return dict(base, state="clear", text="Quiet to moderate", detail="No strong storm expected. GPS and radio should behave normally.")
    except Exception as e:
        print(f"warning: space weather check: {e}", file=sys.stderr)
        return dict(base, state="unknown", text="Could not check", detail="Check the Met Office space weather forecast.")


RADAR_URL = "https://api.cloudflare.com/client/v4/radar/annotations/outages?location=GB&dateRange=2d&format=JSON&limit=25"
CAUSES = {"CABLE_CUT": "cable damage", "POWER_OUTAGE": "a power cut", "WEATHER": "weather", "TECHNICAL_PROBLEM": "a technical fault",
          "GOVERNMENT_DIRECTED": "a government-directed shutdown", "CYBER_ATTACK": "a cyber attack", "MILITARY_ACTION": "military action"}


def check_internet(fixtures, now, token):
    """UK internet disruptions seen by Cloudflare Radar (data CC BY-NC 4.0). Optional: only runs if a token is set.
    Informational only: it never changes a level. 'None' is only shown if the request succeeded and returned a list."""
    if not token and not fixtures:
        return None
    base = {"id": "internet", "name": "UK internet", "url": "https://radar.cloudflare.com/outage-center", "source": "Cloudflare Radar",
            "checked": iso(now), "extra": {"text": "Cable map: TeleGeography", "url": "https://www.submarinecablemap.com/"}}
    try:
        j = json.loads(fetch(RADAR_URL, "internet", fixtures, {"Authorization": f"Bearer {token}"}).decode("utf-8", "replace"))
        ann = (j.get("result") or {}).get("annotations")
        if not isinstance(ann, list) or j.get("success") is False:
            return dict(base, state="unknown", text="Could not read", detail="Check Cloudflare Radar directly.")
        recent = []
        for a in ann:
            start, end = parse_dt(a.get("startDate")), parse_dt(a.get("endDate"))
            if start and now - start <= timedelta(hours=48) and (end is None or now - end <= timedelta(hours=24)):
                recent.append(a)
        if not recent:
            return dict(base, state="clear", text="None in 24 hours", detail="No UK internet disruptions reported.")
        causes = [CAUSES.get(((a.get("outage") or {}).get("outageCause") or "").upper()) for a in recent]
        causes = [c for c in causes if c]
        text = "Disruption reported" + (f": {causes[0]}" if causes else "")
        desc = clean(str(recent[0].get("description") or ""), 200)
        return dict(base, state="notice", text=text, detail=(desc + " " if desc else "") + "Disruptions are often local or short-lived. Check the source.")
    except Exception as e:
        print(f"warning: internet check: {e}", file=sys.stderr)
        return dict(base, state="unknown", text="Could not check", detail="Check Cloudflare Radar directly.")


def check_terror(official, baseline, now):
    lvl = official.get("terror") or baseline["official_terror_baseline"]["level"]
    live = bool(official.get("terror"))
    state = {"LOW": "clear", "MODERATE": "clear", "SUBSTANTIAL": "notice", "SEVERE": "notice", "CRITICAL": "alert"}.get(lvl, "unknown")
    detail = TERROR_TEXT.get(lvl, "")
    if not live:
        detail += f" Last confirmed {baseline['official_terror_baseline']['as_of']}. The live check is unavailable."
    return {"id": "terror", "name": "Terrorism threat level", "state": state, "text": lvl, "detail": detail.strip(),
            "url": "https://www.mi5.gov.uk/threats-and-advice/terrorism-threat-levels", "source": "MI5", "checked": iso(now)}


def http_status(url):
    """Status code of a link, or None if it could not be checked. Many news sites refuse robots, so only 404 and 410 mean 'gone'."""
    from urllib.error import HTTPError
    for method in ("HEAD", "GET"):
        try:
            req = Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(req, timeout=10) as r:
                return r.status
        except HTTPError as e:
            if method == "HEAD" and e.code in (400, 403, 405, 501):
                continue          # some servers do not allow HEAD: try a normal request
            return e.code
        except Exception:
            return None
    return None


def check_links(signals, state, now, status_fn=http_status, recheck_hours=20, dead_after=2):
    """Checks each signal's link at most about once a day. A link counts as dead only after two separate 404 or 410 answers.
    Anything else (blocked, timeout, server error) is treated as 'unknown' and never retires a signal.
    Returns (links_state, retired_ids)."""
    links = dict((state or {}).get("links", {}))
    retired, done = set(), {}
    for sg in signals:
        url = sg["url"]
        rec = dict(links.get(url, {}))
        last = parse_dt(rec.get("checked"))
        if url not in done and not (last and now - last < timedelta(hours=recheck_hours)):
            code = status_fn(url)
            rec["checked"] = iso(now)
            if code in (404, 410):
                rec["fails"] = rec.get("fails", 0) + 1
                print(f"warning: link for signal {sg['id']} returned {code} ({rec['fails']} of {dead_after}): {url}", file=sys.stderr)
            elif code is not None and 200 <= code < 400:
                rec["fails"] = 0
            links[url] = rec
        done[url] = True
        if links.get(url, {}).get("fails", 0) >= dead_after:
            retired.add(sg["id"])
            print(f"warning: signal {sg['id']} retired, its link has been gone on {dead_after} checks: {url}", file=sys.stderr)
    keep = {sg["url"] for sg in signals}
    return {u: r for u, r in links.items() if u in keep}, retired


BBC_FEEDS = [("uk", "https://feeds.bbci.co.uk/news/uk/rss.xml"), ("world", "https://feeds.bbci.co.uk/news/world/rss.xml")]
CRITICAL_NEWS = re.compile(r"\b(terror(?:ist)? attack|terror incident|major incident|state of emergency|national emergency|cobr|emergency alert|blackout|power outage|power cuts?|nationwide outage|cyber[- ]?attack|undersea cable|subsea cable|missile strike|nuclear (?:attack|threat|incident|alert|accident)|evacuat\w+|air raid)\b", re.I)


def fetch_press(enabled, fixtures, now, max_items=3, max_age_hours=6):
    """OPTIONAL and OFF by default. Headline and link only, never any article text, never used for a level.
    Only recent items that match a strict 'critical news' filter. World items must also be about the UK or NATO."""
    if not enabled:
        return []
    found = {}
    for name, url in BBC_FEEDS:
        try:
            rows = parse_feed(fetch(url, "bbc-" + name, fixtures))
        except Exception as e:
            print(f"warning: BBC {name} headlines: {e}", file=sys.stderr)
            continue
        for r in rows:
            when = r["published"]
            if not when or not r["url"] or now - when > timedelta(hours=max_age_hours) or when > now + timedelta(hours=1):
                continue
            if not CRITICAL_NEWS.search(r["title"]):
                continue
            if name == "world" and not (UK_CONTEXT.search(r["title"]) or re.search(r"\bNATO\b", r["title"])):
                continue
            found[r["url"]] = {"title": r["title"], "url": r["url"], "published": iso(when), "source": "BBC News"}
    return sorted(found.values(), key=lambda x: x["published"], reverse=True)[:max_items]


def validate_editorial(signals, baseline):
    """Return a list of problems. Any problem stops publishing, so a typo can never go live."""
    bad = []
    for a in AREAS:
        b = baseline.get("areas", {}).get(a)
        if not b or not isinstance(b.get("level"), int) or not 1 <= b["level"] <= 5:
            bad.append(f"baseline {a}: level must be 1 to 5")
        elif not all(b.get(k) for k in ("status", "reason", "as_of")):
            bad.append(f"baseline {a}: needs status, reason and as_of")
    for s in signals:
        tag = f"signal {s.get('id', '?')}"
        if not safe_url(s.get("url")):
            bad.append(f"{tag}: needs an http(s) link to the original report")
        if not s.get("title") or not s.get("summary"):
            bad.append(f"{tag}: needs a title and a summary in your own words")
        if len(s.get("summary", "")) > 700:
            bad.append(f"{tag}: summary is over 700 characters; keep it short and in your own words")
        if not parse_dt(s.get("date")):
            bad.append(f"{tag}: date must be like 2026-09-10")
        if not s.get("cats") or any(c not in AREAS for c in s["cats"]):
            bad.append(f"{tag}: cats must be from {AREAS}")
    return bad


def validate_history(entries):
    bad = []
    for i, e in enumerate(entries):
        tag = f"history entry {i + 1}"
        if not parse_dt(e.get("date")):
            bad.append(f"{tag}: date must be like 2026-09-19")
        if not isinstance(e.get("level"), int) or not 1 <= e["level"] <= 5:
            bad.append(f"{tag}: level must be 1 to 5")
        if not e.get("title") or not e.get("text"):
            bad.append(f"{tag}: needs a title and text")
        if len(e.get("text", "")) > 400:
            bad.append(f"{tag}: text is over 400 characters")
    return bad


def atom_feed(entries, cfg, now):
    from xml.sax.saxutils import escape as x
    base = (cfg.get("site_url") or "").rstrip("/")
    host = re.sub(r"^https?://", "", base) or "example.invalid"
    updated = iso(parse_dt(entries[0]["date"])) if entries else iso(now)
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"<title>{x(cfg.get('name', 'Wary'))}: changes to the household level</title>",
           f"<subtitle>Every time the editor changes a level, with the reason. No tracking, no sign-up.</subtitle>",
           f"<id>tag:{x(host)},2026:changes</id>", f"<updated>{updated}</updated>"]
    if base:
        out += [f'<link rel="self" href="{x(base)}/history.xml"/>', f'<link rel="alternate" href="{x(base)}/"/>']
    for i, e in enumerate(entries):
        out += ["<entry>", f"<title>{x(e['title'])}</title>", f"<id>tag:{x(host)},{x(e['date'])}:{len(entries) - i}</id>",
                f"<updated>{iso(parse_dt(e['date']))}</updated>", f"<summary>{x(e['text'])}</summary>"]
        out.append(f'<link href="{x(base)}/"/>' if base else "")
        out.append("</entry>")
    out.append("</feed>")
    return "\n".join(o for o in out if o)


def reviewed_date(baseline):
    return max(b["as_of"] for b in baseline["areas"].values())


def build_checks(official, baseline, now):
    """Fixed order. In an editorial-only build nothing was checked, so the strip is left out. Missing checks are skipped, never guessed."""
    ch = official.get("checks")
    if not ch:
        return []
    strip = []
    if ch.get("alerts"):
        strip.append(ch["alerts"])
    strip.append(check_terror(official, baseline, now))
    for key in ("grid", "internet", "space"):
        if ch.get(key):
            strip.append(ch[key])
    return strip


def build_feed(baseline, signals, notice, official, now, history=None, stale_days=60, decay_days=90, signal_max_age_days=365, retired=(), evidence_min=2, evidence_days=30, press=None, area_changes=None):
    """official = {'terror': str|None, 'items': [...], 'weather': {...}, 'used': [...], 'issues': [...]}"""
    tmap = baseline["official_terror_map"]
    areas, levels = [], {}
    for a in AREAS:
        b = baseline["areas"][a]
        bump, why, _url = official.get("bumps", {}).get(a, (1, "", ""))
        terror = official.get("terror")
        follows = a == "security" and terror in tmap   # the terrorism area follows the official MI5 level, up and down
        lowered = False
        if follows:
            base_eff = tmap[terror]
        else:
            base_eff = b["level"]
            when = parse_dt(b["as_of"])
            if base_eff > 2 and when and (now - when) > timedelta(days=decay_days):
                base_eff, lowered = 2, True   # nobody has reviewed it for a long time: settle at Aware rather than stay elevated
        ev = (official.get("evidence") or {}).get(a) if a in EVIDENCE_AREAS else None   # terrorism never uses this: it follows MI5
        ev_level = 3 if (ev and ev["count"] >= evidence_min) else 1
        lvl = max(base_eff, ev_level, bump)
        when_text = parse_dt(b["as_of"])
        text_stale = bool(when_text and (now - when_text) > timedelta(days=decay_days))
        ev_active = bool(ev and ev["count"] >= evidence_min and ev_level > base_eff)
        ev_noted = bool(ev and ev["count"] >= 1 and not ev_active and not follows)
        bump_active = bump > base_eff
        # --- one-line status ---
        if follows:
            status = f"The official UK terrorism threat level is {terror}. {TERROR_TEXT[terror]}"
        elif a == "security":
            status = "The official MI5 threat level could not be read just now."
        elif bump_active:
            status = f"An official statement has raised this area to {LEVEL_NAMES[bump]} for up to 72 hours."
        elif ev_active:
            status = f"{ev['count']} separate official statements in the last {evidence_days} days point to hostile or state-linked activity affecting this area."
        elif ev_noted:
            status = f"One recent official statement noted. It takes {evidence_min} different ones to raise this area."
        elif live_status(a, official.get("checks")):
            status = live_status(a, official.get("checks"))
        elif text_stale:
            status = QUIET_STATUS[a]
        else:
            status = b["status"]
        # --- what is happening: short paragraphs, all from data ---
        pos = []
        if follows:
            pos.append(f"The official UK threat level is {terror} ({TERROR_TEXT[terror].lower().rstrip('.')}). It is read from MI5's website about every 30 minutes and this area follows it up and down.")
        elif a == "security":
            pos.append("The live MI5 reading was not available, so this is the last level we could confirm.")
        if ev_active:
            pos.append(f"Raised to Elevated by official evidence: {ev['count']} different official statements in the last {evidence_days} days name hostile "
                       f"or state-linked activity affecting this area (listed below). The level falls back by itself as they age out.")
        elif ev_noted:
            pos.append(f"{ev['count']} recent official statement is listed below. {evidence_min} different ones within {evidence_days} days raise this area to Elevated.")
        if bump_active:
            pos.append(f"Raised to {LEVEL_NAMES[bump]} for up to 72 hours: {why}.")
        if not pos:
            pos.append("Official sources are not reporting anything unusual for this area at the moment.")
        informative = follows or a == "security" or ev_active or ev_noted or bump_active
        paras = []
        if text_stale:
            paras.append(" ".join(pos))
        elif follows:
            paras.append(" ".join(pos))
            paras.append(f"Background from the editor's last review ({b['as_of']}): {b['reason']}")
        else:
            paras.append(b["reason"])
            if informative:
                paras.append(" ".join(pos))
        inds = indicator_sentences(a, (official.get("checks") or {}), official.get("items"))
        if inds:
            paras.append(" ".join(inds))
        chg = (area_changes or {}).get(a)
        if chg:
            paras.append(f"This area last changed on {fmt_day(chg['date'])}, from {LEVEL_NAMES[chg['from']]} to {LEVEL_NAMES[chg['to']]}.")
        watch = list(WATCH[a])
        if a == "comms" and (official.get("checks") or {}).get("internet"):
            watch.append("UK internet outage data")
        paras.append(f"We watch {_join(watch)}.")
        reason = "\n".join(paras)
        if follows:
            basis = "official"
        elif bump > max(base_eff, ev_level):
            basis = "raised"
        elif ev_level > base_eff:
            basis = "evidence"
        elif lowered:
            basis = "decayed"
        else:
            basis = "editor"
        levels[a] = lvl
        areas.append({"id": a, "name": AREA_NAMES[a], "level": lvl, "baseline": b["level"], "live_level": bump, "status": status,
                      "reason": reason, "baseline_as_of": None if text_stale else b["as_of"], "lowered": lowered, "basis": basis,
                      "evidence": [{"title": e["title"], "source": e["source"], "date": e["date"], "url": e["url"]} for e in (ev["items"] if ev and not follows else [])]})
    top = max(levels.values())
    sigs = []
    for s_ in signals:  # low-touch: older than 45 days becomes "Earlier background"; past the max age (or a dead link) it disappears
        when = parse_dt(s_["date"])
        if s_["id"] in retired or (when and now - when > timedelta(days=signal_max_age_days) and not s_.get("keep")):
            continue
        old = bool(when and now - when > timedelta(days=45))
        sigs.append(dict(s_, auto=False, corroboration=1, background=bool(s_.get("background")) or old))
    for i in official.get("items", []):
        sigs.append({"id": i["id"], "cats": i["cats"], "title": i["title"], "summary": i.get("summary", ""), "source": i["source"], "tier": 1,
                     "date": i["date"], "precision": "day", "url": i["url"], "auto": True, "background": i["background"], "corroboration": 1})
    sigs.sort(key=lambda s: s["date"], reverse=True)
    n = notice or {}
    live_notice = ""
    if n.get("text") and (not n.get("until") or (parse_dt(n["until"]) or now) >= now):
        live_notice = n["text"]
    return {
        "mode": "live", "generated_at": iso(now),
        "overall": {"level": top, "name": LEVEL_NAMES[top], "drivers": [a for a in AREAS if levels[a] == top]},
        "areas": areas, "signals": sigs, "notice": live_notice,
        "weather": official.get("weather", {}),
        "official": {"terror_level": official.get("terror") or baseline["official_terror_baseline"]["level"],
                     "live_checked": bool(official.get("terror")), "as_of": baseline["official_terror_baseline"]["as_of"],
                     "ni_level": baseline.get("official_ni_baseline", {}).get("level", "")},
        "sources_used": official.get("used", []), "data_issues": official.get("issues", []),
        "checks": build_checks(official, baseline, now),
        "press": press or [],
        "reviewed": reviewed_date(baseline) if any(x["basis"] == "editor" and x["level"] > 2 for x in areas) else None,
        "stale_days": stale_days, "decay_days": decay_days,
        "auto_notices": official.get("auto_notices", []),
        "history": sorted(history or [], key=lambda e: e["date"], reverse=True)[:12],
    }


CABLE_DAMAGE = re.compile(r"(?=.*(?:undersea|subsea|submarine|seabed))(?=.*(?:cable|pipeline|infrastructure))(?=.*(?:damag|sever|\bcut\b|sabotag|incident|attack|break))", re.I)
ATTACK_ON_UK = re.compile(r"attack (?:on|against) (?:the )?(?:UK|United Kingdom|Britain)|(?:UK|United Kingdom|Britain)(?: is| has been)? under attack|attack on British soil", re.I)


def load_state(path):
    return load_json(path, {"first_seen": {}}) or {"first_seen": {}}


def update_state(state, active_keys, now, terror_last=None, links=None, evidence=None, levels_last=None, auto_history=None, area_changes=None):
    """Remember when each trigger was FIRST seen. A trigger that disappears is forgotten, so the timer restarts if it returns.
    Also remembers the last accepted MI5 level, so the site can follow it up and down over the months."""
    first = state.get("first_seen", {})
    return {"first_seen": {k: first.get(k) or iso(now) for k in active_keys}, "terror_last": terror_last or state.get("terror_last"),
            "links": links if links is not None else state.get("links", {}),
            "evidence": evidence if evidence is not None else state.get("evidence", []),
            "levels_last": levels_last if levels_last is not None else state.get("levels_last"),
            "auto_history": auto_history if auto_history is not None else state.get("auto_history", []),
            "area_changes": area_changes if area_changes is not None else state.get("area_changes", {})}


def held(state, key, now, hours):
    t = parse_dt((state or {}).get("first_seen", {}).get(key))
    return bool(t and now - t >= timedelta(hours=hours))


EVIDENCE_AREAS = ["energy", "cyber", "comms", "military", "supply"]   # terrorism follows MI5 instead
HOSTILE = re.compile(r"\b(russia|russian|kremlin|gru|moscow|iran|iranian|north korea\w*|state[- ]linked|state[- ]backed|state[- ]sponsored|hostile state|hostile actors?|hostile activity|hybrid (?:threat|activity|warfare)|chinese state|china[- ]linked)\b", re.I)
THREAT = re.compile(r"\b(attacks?|sabotage|threaten\w*|disrupt\w*|target\w*|interfer\w*|espionage|hack\w*|threats?|malicious|compromis\w*|incidents?|campaigns?|jamming|spoofing)\b", re.I)
UK_CONTEXT = re.compile(r"\b(UK|United Kingdom|Britain|British|national|CNI)\b")
AREA_TERMS = {k: re.compile(v, re.I) for k, v in {
    "cyber": r"cyber|ransomware|malware|\bddos\b|data breach|\bhack\w*|\bncsc\b",
    "comms": r"undersea|subsea|seabed|submarine cable|\bcables?\b|\bgnss\b|\bgps\b|telecom|satellite|fibre",
    "energy": r"\benergy\b|electricity|\bgrid\b|\bgas\b|pipeline|interconnector|power station|substation|\bfuel\b",
    "military": r"\bnato\b|nuclear|missile|submarine|warship|airspace|\bdrones?\b|armed forces|ministry of defence|\bnavy\b|\braf\b",
    "supply": r"\bfood\b|water (?:supply|compan|utilit)|supply chain|\bports?\b|logistics|shortage",
}.items()}
_STOP = set("the a an and or of to in on for with from by at as is are was were be been has have had this that these those after over into its it's their about says said new more than us uk".split())


def evidence_areas(item):
    """Which areas an OFFICIAL item counts as evidence for. It must name a hostile or state-linked actor, describe a threat,
    touch the area, and be about the UK (not, say, the war in Ukraine)."""
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    if not (HOSTILE.search(text) and THREAT.search(text)):
        return set()
    if not ("NCSC" in item.get("source", "") or UK_CONTEXT.search(text)):
        return set()
    return {a for a, rx in AREA_TERMS.items() if rx.search(text)}


def _tok(title):
    return {w for w in re.findall(r"[a-z0-9]{4,}", title.lower()) if w not in _STOP}


def cluster_titles(entries):
    """Group near-duplicate statements so one event counts once. Entries newest first. Returns a list of clusters (newest first)."""
    groups = []
    for e in entries:
        tk = _tok(e["title"])
        for g in groups:
            inter, union = len(tk & g["tokens"]), len(tk | g["tokens"]) or 1
            if inter >= 4 or inter / union >= 0.5:
                g["members"].append(e)
                g["tokens"] |= tk
                break
        else:
            groups.append({"tokens": set(tk), "members": [e]})
    return [g["members"] for g in groups]


def compute_evidence(items, state, now, window_days=30):
    """Remember qualifying official items for the rolling window (the GitHub cache keeps them between runs), then count
    DIFFERENT statements per area. Returns ({area: {'count', 'items'}}, store_to_save)."""
    store = {e["id"]: e for e in (state or {}).get("evidence", [])}
    for i in items:
        areas = evidence_areas(i)
        if areas:
            store[i["id"]] = {"id": i["id"], "date": i["date"], "title": i["title"], "source": i["source"], "url": i["url"], "areas": sorted(areas)}
    cutoff = now - timedelta(days=window_days)
    store = {k: v for k, v in store.items() if (parse_dt(v["date"]) or cutoff) >= cutoff}
    by_area = {}
    for a in EVIDENCE_AREAS:
        rows = sorted((v for v in store.values() if a in v["areas"]), key=lambda v: v["date"], reverse=True)
        clusters = cluster_titles(rows)
        by_area[a] = {"count": len(clusters), "items": [c[0] for c in clusters][:4]}
    return by_area, list(store.values())


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
QUIET_STATUS = {
    "energy": "No official warnings about power, gas or fuel right now.",
    "cyber": "Nothing unusual in official cyber reporting right now.",
    "comms": "Cables, GPS and phone networks look normal in the sources we check.",
    "military": "No official statements pointing to a military threat to UK homes right now.",
    "supply": "No official warnings about food, water or supply chains right now.",
}
WATCH = {
    "energy": ["the energy department's announcements", "the grid operator's system warnings", "space weather"],
    "cyber": ["the National Cyber Security Centre", "the Cabinet Office", "US CISA advisories"],
    "comms": ["the Ministry of Defence", "the Department for Transport", "the Maritime and Coastguard Agency", "space weather"],
    "military": ["the Ministry of Defence", "the Foreign Office", "the Cabinet Office"],
    "supply": ["the Department for Environment, Food and Rural Affairs", "the Drinking Water Inspectorate", "the Department for Transport"],
    "security": ["MI5's threat level", "the Home Office", "GOV.UK Emergency Alerts"],
}


def fmt_day(v):
    d = v if isinstance(v, datetime) else parse_dt(v)
    return f"{d.day} {MONTHS[d.month - 1]}" if d else ""


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def live_status(area, checks):
    """A one-line status taken from a live monitor, when a monitor has something to say. Otherwise None."""
    c = checks or {}
    g, n = c.get("grid"), c.get("internet")
    if area == "energy" and g and g["state"] == "notice":
        return "The grid operator has issued a routine notice. Nothing points to a shortage."
    if area == "energy" and g and g["state"] == "alert":
        return "The grid operator's latest warning mentions emergency measures."
    if area == "comms" and n and n["state"] == "notice":
        return "Some UK internet disruption is being reported. The cause is not confirmed."
    return None


def indicator_sentences(area, checks, items):
    """What the live monitors say about this area right now. Every sentence comes from data that was actually read."""
    c = checks or {}
    out = []

    def latest(cat):
        rows = [i for i in (items or []) if cat in i.get("cats", [])]
        return max(rows, key=lambda i: i["date"]) if rows else None

    def space():
        sp = c.get("space")
        if sp and sp.get("state") in ("notice", "alert"):
            return "Space weather is active. That can disturb GPS and radio and, rarely, the power grid, so it is worth checking before assuming a hostile cause."
        return None

    if area == "energy":
        g = c.get("grid")
        if g and g["state"] == "clear":
            out.append("The grid operator has issued no system warnings in the last 24 hours.")
        elif g and g["state"] == "notice":
            out.append("The grid operator issued a routine market notice in the last 24 hours. These are common in winter and do not mean supplies are at risk.")
        elif g and g["state"] == "alert":
            out.append("The grid operator's latest warning mentions emergency measures. Its own updates are the place to look.")
        if space():
            out.append(space())
    elif area == "cyber":
        i = latest("cyber")
        if i:
            out.append(f"The latest official cyber item was published on {fmt_day(i['date'])}: {i['title']}.")
    elif area == "comms":
        n = c.get("internet")
        if n and n["state"] == "clear":
            out.append("Cloudflare Radar reports no UK internet disruptions in the last 24 hours.")
        elif n and n["state"] == "notice":
            cause = n["text"].split(": ", 1)[1].lower() if ": " in n["text"] else ""
            out.append("Cloudflare Radar reports a UK internet disruption" + (f", which it links to {cause}" if cause else "") + ". Disruptions like this are often local or short-lived.")
        if space():
            out.append(space())
    elif area == "military":
        i = latest("military")
        if i:
            out.append(f"The latest official item on this area was published on {fmt_day(i['date'])}: {i['title']}.")
    elif area == "supply":
        i = latest("supply")
        if i:
            out.append(f"The latest official item on this area was published on {fmt_day(i['date'])}: {i['title']}.")
    elif area == "security":
        al = c.get("alerts")
        if al and al["state"] == "clear":
            out.append("GOV.UK shows no live Emergency Alerts.")
        elif al:
            out.append("The Emergency Alerts page could not be read just now, so check GOV.UK if you are worried.")
    return out


BASIS_TEXT = {"official": "following the official MI5 level", "evidence": "official evidence", "raised": "an official trigger",
              "decayed": "the standing level settled after 90 days", "editor": "the standing level"}


def track_changes(prev, areas, area_changes, now):
    """Remember, for each area, when its level last moved (used for the 'this area last changed on...' sentence)."""
    out = dict(area_changes or {})
    if prev and prev.get("areas"):
        for x in areas:
            old = prev["areas"].get(x["id"])
            if old is not None and old != x["level"]:
                out[x["id"]] = {"date": iso(now), "from": old, "to": x["level"]}
    return out


def level_changes(prev, areas, overall, now):
    """An automatic change-log entry when any level moved since the last run. No previous run (or a lost cache) means no entry."""
    if not prev or "overall" not in prev:
        return []
    moved = [a for a in areas if prev.get("areas", {}).get(a["id"]) not in (None, a["level"])]
    if not moved and prev["overall"] == overall:
        return []
    old = prev["overall"]
    if old != overall:
        title = f"Overall level {'raised' if overall > old else 'lowered'}: {LEVEL_NAMES[old]} to {LEVEL_NAMES[overall]}"
    else:
        title = "Area levels changed"
    lines = [f"{a['name']}: {LEVEL_NAMES[prev['areas'][a['id']]]} to {LEVEL_NAMES[a['level']]} ({BASIS_TEXT.get(a.get('basis'), 'the rules')})" for a in moved]
    text = "Automatic entry. " + ("; ".join(lines) + "." if lines else "Only the overall level moved.")
    return [{"date": now.strftime("%Y-%m-%d"), "level": overall, "title": title, "text": text[:400], "auto": True}]


def auto_rules(items, checks, now, terror=None, state=None, hold_hours=6):
    """Low-touch mode. Only unambiguous OFFICIAL triggers act on their own, and each lasts 72 hours from the source's date.
    Nothing from the press can appear here, and nothing can set level 5.
    Returns (bumps {area: (level, why, url)}, notices [{text, url}])."""
    bumps, notices, active = {}, [], set()
    cobr_seen, attack_items = False, []
    for i in items:
        when = parse_dt(i["date"])
        if not when or now - when > timedelta(hours=72):
            continue
        if re.search(r"nationally significant", i["title"], re.I) and "cyber" in i["cats"]:
            bumps["cyber"] = (4, f"{i['source']}: {i['title']}", i["url"])
        if re.search(r"\bCOBR\b", i["title"]):
            cobr_seen = True
            notices.append({"text": f"The government has convened COBR, its emergency committee. {i['source']}: {i['title']}", "url": i["url"]})
        if CABLE_DAMAGE.search(i["title"]):
            bumps["comms"] = (4, f"{i['source']}: {i['title']}", i["url"])
        if ATTACK_ON_UK.search(i["title"]) and now - when <= timedelta(hours=24):
            attack_items.append(i)
    grid = (checks or {}).get("grid", {})
    if grid.get("state") == "alert":
        bumps["energy"] = (4, "the grid operator's system warning mentions demand control or load shedding", "https://bmrs.elexon.co.uk/")
    # Level 5 needs a clear official signal AND for it to hold. Until it has held it shows as High (4).
    if terror == "CRITICAL":
        active.add("mi5-critical")
        lvl = 5 if held(state, "mi5-critical", now, hold_hours) else 4
        bumps["security"] = (lvl, f"the official terrorism threat level is CRITICAL (an attack is expected imminently){' and has held for over ' + format(hold_hours, 'g') + ' hours' if lvl == 5 else ''}", "https://www.mi5.gov.uk/threats-and-advice/terrorism-threat-levels")
    if cobr_seen and attack_items:  # two separate official statements: an attack on the UK named AND COBR convened
        active.add("gov-attack")
        lvl = 5 if held(state, "gov-attack", now, hold_hours) else 4
        top = attack_items[0]
        areas = [a for a in ("security", "military") if a in top["cats"]] or ["military"]
        for a in areas:
            if lvl > bumps.get(a, (1,))[0]:
                bumps[a] = (lvl, f"{top['source']}: {top['title']}, with COBR convened{' and holding for over ' + format(hold_hours, 'g') + ' hours' if lvl == 5 else ''}", top["url"])
    return bumps, notices, active


def gather_official(src_doc, fixtures, now, last_terror=None, state=None, hold_hours=6, radar_token=""):
    out = {"terror": None, "items": [], "weather": {}, "used": [], "issues": [], "checks": {}}
    items = []
    for s in src_doc["sources"]:
        try:
            data = fetch(s["url"], s["id"], fixtures)
            if s["kind"] == "mi5_level":
                found, contextual = read_terror_level(data)
                ok, pending = accept_terror(found, contextual, last_terror, state, now, hold_hours)
                if pending:
                    out.setdefault("pending_keys", set()).add(pending)
                if ok:
                    out["terror"] = found
                else:
                    print(f"warning: MI5 page read as {found} but the last accepted level is {last_terror}. Ignored for now. Check the page by hand.", file=sys.stderr)
                    out["issues"].append("MI5 threat level (reading looked wrong or is a large jump still being confirmed, so the last accepted level is shown)")
                    continue
            else:
                for it in parse_feed(data):
                    if not it["published"] or not it["url"]:
                        continue
                    text = f"{it['title']} {it['summary']}"
                    cats = [a for a in AREAS if RX[a].search(text)]
                    if not cats and s.get("default_area"):
                        cats = [s["default_area"]]
                    age = now - it["published"]
                    if not cats or CALM.search(it["title"]) or age > timedelta(days=30) or age < timedelta(hours=-6):
                        continue
                    items.append({"id": "o-" + re.sub(r"\W+", "", it["url"])[-14:], "cats": cats[:3], "title": it["title"], "summary": it["summary"], "source": s["name"],
                                  "date": iso(it["published"]), "url": it["url"], "background": age > timedelta(days=14)})
            out["used"].append({"name": s["name"], "licence": s["licence"], "url": s["url"].split("?")[0]})
        except Exception as e:
            out["issues"].append(s["name"])
            print(f"warning: {s['name']}: {e}", file=sys.stderr)
    items.sort(key=lambda i: i["date"], reverse=True)
    out["all_items"] = items
    out["items"] = items[:14]
    for r in src_doc.get("met_regions", []):
        try:
            warns = []
            for it in parse_feed(fetch(r["url"], "met-" + r["code"], fixtures)):
                if "warning" in it["title"].lower() and not it["title"].lower().startswith("no "):
                    warns.append({"title": it["title"], "url": it["url"], "published": iso(it["published"])})
            out["weather"][r["code"]] = warns
        except Exception as e:
            print(f"warning: Met Office {r['name']}: {e}", file=sys.stderr)
    out["checks"]["alerts"] = check_alerts(fixtures, now)
    out["checks"]["grid"] = check_grid(fixtures, now)
    out["checks"]["space"] = check_space(fixtures, now)
    net = check_internet(fixtures, now, radar_token)
    if net:
        out["checks"]["internet"] = net
    out["bumps"], out["auto_notices"], out["active_keys"] = auto_rules(items, out["checks"], now, out.get("terror"), state, hold_hours)
    out["active_keys"] = set(out["active_keys"]) | set(out.pop("pending_keys", set()))
    if out["checks"]["alerts"]["state"] != "unknown":
        out["used"].append({"name": "GOV.UK Emergency Alerts status", "licence": "OGL v3.0", "url": ALERTS_URL})
    if out["checks"]["grid"]["state"] != "unknown":
        out["used"].append({"name": "Elexon BMRS system warnings", "licence": "Elexon open data licence (attribution required)", "url": "https://bmrs.elexon.co.uk/"})
    if out["checks"].get("internet") and out["checks"]["internet"]["state"] != "unknown":
        out["used"].append({"name": "Cloudflare Radar (UK internet outages)", "licence": "CC BY-NC 4.0, non-commercial use with attribution", "url": "https://radar.cloudflare.com/outage-center"})
    if out["checks"]["space"]["state"] != "unknown":
        out["used"].append({"name": "NOAA Space Weather Prediction Center (levels)", "licence": "US government data, free to use", "url": "https://www.swpc.noaa.gov/"})
    if out["weather"]:
        out["used"].append({"name": "Met Office weather warnings", "licence": "OGL v3.0", "url": "https://www.metoffice.gov.uk/weather/warnings-and-advice"})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures")
    ap.add_argument("--out", default=os.path.join(ROOT, "site", "feed.json"))
    ap.add_argument("--state", default=os.path.join(ROOT, "data", "state.json"), help="remembers when a level-5 trigger was first seen")
    ap.add_argument("--editorial-only", action="store_true", help="skip all fetching (used to build the offline fallback)")
    a = ap.parse_args()
    now = now_utc()
    baseline = load_json(os.path.join(ROOT, "editorial", "baseline.json"))
    signals = load_json(os.path.join(ROOT, "editorial", "signals.json"))["signals"]
    cfg = load_json(os.path.join(ROOT, "site.json"), {})
    history = (load_json(os.path.join(ROOT, "editorial", "history.json"), {}) or {}).get("entries", [])
    problems = validate_editorial(signals, baseline) + validate_history(history)
    if problems:
        print("Editorial files have problems. Nothing was published:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1
    retired, state, links_state, ev_store, press = set(), {}, None, None, []
    if a.editorial_only:
        official = {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}
    else:
        state = load_state(a.state)
        official = gather_official(load_json(os.path.join(ROOT, "official_sources.json")), a.fixtures, now,
                                   state.get("terror_last") or baseline["official_terror_baseline"]["level"], state, float(cfg.get("hold_hours", 6)),
                                   os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        ev_by_area, ev_store = compute_evidence(official.pop("all_items", []), state, now, int(cfg.get("evidence_window_days", 30)))
        official["evidence"] = ev_by_area
        links_state, retired = ({}, set())
        if not a.fixtures:  # never check links in offline test runs
            links_state, retired = check_links(signals, state, now)
        press = fetch_press(bool(cfg.get("bbc_headlines", False)), a.fixtures, now)
        if not cfg.get("auto_headlines", True):
            official["items"] = []
        if not cfg.get("auto_levels", True):  # the emergency brake: no automatic raising of any level, and no automatic notices
            official["bumps"], official["auto_notices"], official["evidence"] = {}, [], {}
    notice = load_json(os.path.join(ROOT, "editorial", "notice.json"))
    stale_banner_days = int(cfg.get("stale_days", 60)) if cfg.get("stale_banner", False) else 0     # off by default: the site is meant to run itself

    def make(changes):
        return build_feed(baseline, signals, notice, official, now, history, stale_banner_days, int(cfg.get("decay_days", 90)),
                          int(cfg.get("signal_max_age_days", 365)), retired, int(cfg.get("evidence_min_items", 2)),
                          int(cfg.get("evidence_window_days", 30)), press, changes)

    changes_map = dict(state.get("area_changes") or {})
    feed = make(changes_map)
    if not a.editorial_only:
        new_map = track_changes(state.get("levels_last"), feed["areas"], changes_map, now)
        if new_map != changes_map:      # something moved this run: rebuild so the wording mentions it straight away
            changes_map = new_map
            feed = make(changes_map)
    all_history = list(history)
    if not a.editorial_only:
        new_entries = level_changes(state.get("levels_last"), feed["areas"], feed["overall"]["level"], now)
        auto_hist = (new_entries + state.get("auto_history", []))[:60]
        all_history = list(history) + auto_hist
        feed["history"] = sorted(all_history, key=lambda e: e["date"], reverse=True)[:12]
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as f:
            json.dump(update_state(state, official.get("active_keys", set()), now, official.get("terror"), links_state or None, ev_store,
                                   {"overall": feed["overall"]["level"], "areas": {x["id"]: x["level"] for x in feed["areas"]}}, auto_hist, changes_map), f, indent=1)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    with open(os.path.join(os.path.dirname(a.out), "history.xml"), "w", encoding="utf-8") as f:
        f.write(atom_feed(sorted(all_history, key=lambda e: e["date"], reverse=True), cfg, now))
    print(f"wrote {a.out}: level {feed['overall']['level']} ({feed['overall']['name']}), "
          f"{len(feed['signals'])} signals, {len(official['issues'])} official sources unavailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
