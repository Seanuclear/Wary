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
    """Fixed order. In an editorial-only build nothing was checked, so the strip is left out."""
    if not official.get("checks"):
        return []
    strip = [official["checks"]["alerts"], check_terror(official, baseline, now), official["checks"]["grid"]]
    if official["checks"].get("internet"):
        strip.append(official["checks"]["internet"])
    if official["checks"].get("space"):
        strip.append(official["checks"]["space"])
    return strip


def build_feed(baseline, signals, notice, official, now, history=None, stale_days=60, decay_days=90):
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
        lvl = max(base_eff, bump)
        status, reason = b["status"], b["reason"]
        if follows:
            status = f"The official UK terrorism threat level is {terror}. {TERROR_TEXT[terror]}"
            reason = (f"The official UK threat level is {terror} ({TERROR_TEXT[terror].lower().rstrip('.')}). It is read from MI5's website every 30 minutes "
                      f"and this area follows it up and down. Background from the editor's last review ({b['as_of']}): {b['reason']}")
        elif a == "security":
            reason += " (The live MI5 reading was not available, so this is the editor's last assessment.)"
        if lowered:
            reason += f" Lowered automatically to Aware because it has not been reviewed since {b['as_of']}."
        if bump > base_eff:
            reason += f" Raised automatically to {LEVEL_NAMES[bump]} for up to 72 hours: {why}."
        levels[a] = lvl
        areas.append({"id": a, "name": AREA_NAMES[a], "level": lvl, "baseline": b["level"], "live_level": bump, "status": status,
                      "reason": reason, "baseline_as_of": b["as_of"], "lowered": lowered, "basis": "official" if follows else ("raised" if bump > base_eff else ("decayed" if lowered else "editor"))})
    top = max(levels.values())
    sigs = []
    for s_ in signals:  # low-touch: anything older than 45 days moves to "Earlier background" by itself
        when = parse_dt(s_["date"])
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
        "reviewed": reviewed_date(baseline),
        "stale_days": stale_days, "decay_days": decay_days,
        "auto_notices": official.get("auto_notices", []),
        "history": sorted(history or [], key=lambda e: e["date"], reverse=True)[:12],
    }


CABLE_DAMAGE = re.compile(r"(?=.*(?:undersea|subsea|submarine|seabed))(?=.*(?:cable|pipeline|infrastructure))(?=.*(?:damag|sever|\bcut\b|sabotag|incident|attack|break))", re.I)
ATTACK_ON_UK = re.compile(r"attack (?:on|against) (?:the )?(?:UK|United Kingdom|Britain)|(?:UK|United Kingdom|Britain)(?: is| has been)? under attack|attack on British soil", re.I)


def load_state(path):
    return load_json(path, {"first_seen": {}}) or {"first_seen": {}}


def update_state(state, active_keys, now, terror_last=None):
    """Remember when each trigger was FIRST seen. A trigger that disappears is forgotten, so the timer restarts if it returns.
    Also remembers the last accepted MI5 level, so the site can follow it up and down over the months."""
    first = state.get("first_seen", {})
    return {"first_seen": {k: first.get(k) or iso(now) for k in active_keys}, "terror_last": terror_last or state.get("terror_last")}


def held(state, key, now, hours):
    t = parse_dt((state or {}).get("first_seen", {}).get(key))
    return bool(t and now - t >= timedelta(hours=hours))


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
    if a.editorial_only:
        official = {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}
    else:
        state = load_state(a.state)
        official = gather_official(load_json(os.path.join(ROOT, "official_sources.json")), a.fixtures, now,
                                   state.get("terror_last") or baseline["official_terror_baseline"]["level"], state, float(cfg.get("hold_hours", 6)),
                                   os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as f:
            json.dump(update_state(state, official.get("active_keys", set()), now, official.get("terror")), f, indent=1)
        if not cfg.get("auto_headlines", True):
            official["items"] = []
        if not cfg.get("auto_levels", True):  # the emergency brake: no automatic raising of any level, and no automatic notices
            official["bumps"], official["auto_notices"] = {}, []
    feed = build_feed(baseline, signals, load_json(os.path.join(ROOT, "editorial", "notice.json")), official, now, history,
                      int(cfg.get("stale_days", 60)), int(cfg.get("decay_days", 90)))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    with open(os.path.join(os.path.dirname(a.out), "history.xml"), "w", encoding="utf-8") as f:
        f.write(atom_feed(sorted(history, key=lambda e: e["date"], reverse=True), cfg, now))
    print(f"wrote {a.out}: level {feed['overall']['level']} ({feed['overall']['name']}), "
          f"{len(feed['signals'])} signals, {len(official['issues'])} official sources unavailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
