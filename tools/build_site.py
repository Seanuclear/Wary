#!/usr/bin/env python3
"""Builds the static site into site/ from src/, site.json and the editorial files.

    python3 tools/build_site.py             # normal build (the GitHub workflow runs this, then publish.py)
    python3 tools/build_site.py --preview   # same, with a 'Preview' banner on the offline copy (for sharing a mock-up)

The page embeds an offline copy of your editorial content, so it still shows something sensible if the
live feed cannot be loaded.
"""
import argparse
import html
import re
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publish  # noqa: E402


SCEN = {  # level -> (terror level, notice, {area: (level, status)}). ILLUSTRATIVE, not real events.
    1: ("MODERATE", "", {
        "energy": (1, "Supply is normal. No warnings from the grid operator."),
        "cyber": (1, "No unusual reports of attacks on UK services."),
        "comms": (1, "Networks and cables are working normally."),
        "security": (1, "The official threat level is lower than it has been. Daily life is unaffected."),
        "military": (1, "No public sign of any threat to UK homes."),
        "supply": (1, "Shops and utilities are running as normal.")}),
    2: ("SUBSTANTIAL", "", {
        "energy": (2, "Gas prices are under strain abroad. Supply is normal."),
        "cyber": (2, "Attacks on organisations are reported from time to time. Services are running."),
        "comms": (1, "Some undersea activity reported abroad. UK cables are working normally."),
        "security": (2, "The official terrorism threat level is SUBSTANTIAL, meaning an attack is likely."),
        "military": (2, "International tension is high. No change to advice for households."),
        "supply": (1, "No shortages. Prices may rise.")}),
    4: ("SEVERE", "Cyber attacks and Cables and GPS raised to High. Officials have confirmed disruption to some services.", {
        "energy": (3, "Warnings of tight supply this week. Outages are possible in some areas."),
        "cyber": (4, "A major attack has disrupted some banks and public services. Fixes are under way."),
        "comms": (4, "Several undersea cables are damaged. Internet and phone services are slow in places."),
        "security": (3, "The official terrorism threat level is SEVERE, meaning an attack is highly likely."),
        "military": (3, "Tension has risen sharply. Officials have urged calm and preparation."),
        "supply": (3, "Some shops report shortages after panic buying. Officials say supply is adequate.")}),
    5: ("SEVERE", "Official emergency instructions are in force. Follow the emergency services, your council and the BBC. This page cannot show local detail.", {
        "energy": (4, "Widespread power cuts reported. Follow your network operator and your council."),
        "cyber": (4, "Major services are down. Do not rely on online banking or card payments."),
        "comms": (4, "Internet and mobile networks are badly disrupted. Use the radio for information."),
        "security": (3, "The official terrorism threat level is SEVERE, meaning an attack is highly likely."),
        "military": (5, "A national emergency has been declared. Follow instructions from the emergency services and your council."),
        "supply": (4, "Shortages are widespread. Do not panic buy. Follow official guidance on supplies.")}),
}
NAMES = {1: "Routine", 2: "Aware", 3: "Elevated", 4: "High", 5: "Critical"}

SPACE = {1: ("clear", "Quiet to moderate", "No strong storm expected. GPS and radio should behave normally."), 2: ("clear", "Quiet to moderate", "No strong storm expected. GPS and radio should behave normally."),
         3: ("clear", "Quiet to moderate", "No strong storm expected. GPS and radio should behave normally."),
         4: ("notice", "Strong storm (G3)", "Expected or under way. It can disturb GPS and radio, and rarely the power grid. Check the Met Office before assuming a hostile cause."),
         5: ("clear", "Quiet to moderate", "No strong storm expected. GPS and radio should behave normally.")}
INTERNET = {4: ("notice", "Disruption reported: cable damage", "Traffic dropped on some UK networks. Disruptions are often local or short-lived. Check the source."),
            5: ("notice", "Disruption reported: cable damage", "Traffic dropped on some UK networks. Disruptions are often local or short-lived. Check the source.")}
CLEAR_NET = ("clear", "None in 24 hours", "No UK internet disruptions reported.")
# Illustrative Live checks per scenario: (alerts, grid). The terror check follows the scenario's threat level.
CHECKS = {
    1: (("clear", "None current", "Sent by the emergency services and government. None are live."), ("clear", "No notices in 24 hours", "Electricity margins look normal.")),
    2: (("clear", "None current", "Sent by the emergency services and government. None are live."), ("clear", "No notices in 24 hours", "Electricity margins look normal.")),
    3: (("clear", "None current", "Sent by the emergency services and government. None are live."), ("clear", "No notices in 24 hours", "Electricity margins look normal.")),
    4: (("clear", "None current", "Sent by the emergency services and government. None are live."), ("notice", "Market notice issued", "These are routine most winters and do not mean supplies are at risk.")),
    5: (("alert", "Alert sent to some areas", "Read it on GOV.UK now and follow its instructions if it covers your area."), ("alert", "Emergency measures mentioned", "Check National Energy System Operator updates now.")),
}


def handsoff_feeds(baseline, history):
    """Two illustrative days run through the REAL publishing rules: no written signals, generated wording, BBC strip on."""
    from datetime import timedelta
    now = publish.now_utc()
    iso = publish.iso
    old = json.loads(json.dumps(baseline))
    for a in old["areas"].values():
        a["as_of"] = (now - timedelta(days=200)).strftime("%Y-%m-%d")     # hands-off: the editor's wording is long out of date
    def item(iid, title, summary, source, days, cats):
        return {"id": iid, "title": "Illustrative: " + title, "summary": summary, "source": source + " (illustrative)", "url": "https://www.gov.uk/",
                "date": iso(now - timedelta(days=days)), "cats": cats, "background": False}
    def checks(alerts, grid, space, net, terror):
        base = {"checked": iso(now)}
        c = {"alerts": dict(base, id="alerts", name="Emergency Alerts", url="https://www.gov.uk/alerts", source="GOV.UK", state=alerts[0], text=alerts[1], detail="Sent by the emergency services and government. None are live." if alerts[0] == "clear" else alerts[2]),
             "grid": dict(base, id="grid", name="Electricity grid notices", url="https://bmrs.elexon.co.uk/", source="Elexon BMRS", state=grid[0], text=grid[1], detail=grid[2],
                          extra={"text": "Gas notices: National Gas", "url": publish.GAS_URL}),
             "space": dict(base, id="space", name="Space weather", url=publish.MET_SPACE_URL, source="Met Office", state=space[0], text=space[1], detail=space[2], extra={"text": "Levels from NOAA", "url": "https://www.swpc.noaa.gov/"}),
             "internet": dict(base, id="internet", name="UK internet", url="https://radar.cloudflare.com/outage-center", source="Cloudflare Radar", state=net[0], text=net[1], detail=net[2],
                              extra={"text": "Cable map: TeleGeography", "url": "https://www.submarinecablemap.com/"})}
        return c
    quiet_items = [item("q1", "NCSC publishes guidance for small businesses on backing up data", "New advice on keeping offline backups.", "NCSC news", 3, ["cyber"]),
                   item("q2", "Defence Secretary sets out the defence investment plan", "Statement to Parliament.", "GOV.UK: Ministry of Defence", 6, ["military"])]
    busy_items = [item("b1", "NCSC warns of Russian state-linked cyber campaign targeting UK organisations", "The NCSC and partners have published details of the activity and how to defend against it.", "NCSC news", 2, ["cyber"]),
                  item("b2", "Iranian hackers target UK water utilities in cyber attack warning, Cabinet Office says", "Operators are urged to check remote access.", "GOV.UK: Cabinet Office", 9, ["cyber", "supply"]),
                  item("b3", "Royal Navy shadows Russian vessels threatening UK undersea cables", "The Ministry of Defence said the vessels were monitored throughout and no damage was reported.", "GOV.UK: Ministry of Defence", 4, ["comms", "military"]),
                  item("b4", "Statement on state-linked interference with UK satellite navigation", "Officials are investigating reports of interference.", "GOV.UK: Department for Transport", 11, ["comms"]),
                  item("b5", "Department for Transport publishes port security guidance", "Updated guidance for port operators.", "GOV.UK: Department for Transport", 1, ["supply"])]
    empty = {"terror": None, "weather": {}, "used": [], "issues": [], "bumps": {}, "auto_notices": []}
    days = {}
    for key, terror, items, chk in (
            ("quiet", "SUBSTANTIAL", quiet_items, checks(("clear", "None current", ""), ("clear", "No notices in 24 hours", "Electricity margins look normal."), ("clear", "Quiet to moderate", "No strong storm expected. GPS and radio should behave normally."),
                                                          ("clear", "None in 24 hours", "No UK internet disruptions reported."), "SUBSTANTIAL")),
            ("busy", "SEVERE", busy_items, checks(("clear", "None current", ""), ("notice", "Market notice issued", "These are routine most winters and do not mean supplies are at risk."),
                                                  ("notice", "Strong storm (G3)", "Expected or under way. It can disturb GPS and radio, and rarely the power grid. Check the Met Office before assuming a hostile cause."),
                                                  ("notice", "Disruption reported: cable damage", "Traffic dropped on some UK networks. Disruptions are often local or short-lived. Check the source."), "SEVERE"))):
        ev, _store = publish.compute_evidence(items, {}, now)
        off = dict(empty, terror=terror, items=items, evidence=ev, checks=chk)
        press = [] if key == "quiet" else [
            {"title": "Illustrative headline: major incident declared after power cuts across the region", "url": "https://www.bbc.co.uk/news", "published": iso(now - timedelta(hours=1)), "source": "BBC News (illustrative)"},
            {"title": "Illustrative headline: cyber attack on UK water firms confirmed", "url": "https://www.bbc.co.uk/news", "published": iso(now - timedelta(hours=3)), "source": "BBC News (illustrative)"}]
        changes = {"cyber": {"date": iso(now - timedelta(days=1)), "from": 2, "to": 3}, "comms": {"date": iso(now - timedelta(days=2)), "from": 2, "to": 3}} if key == "busy" else {}
        days[key] = publish.build_feed(old, [], {}, off, now, history, press=press, area_changes=changes)
    prev = {"overall": days["quiet"]["overall"]["level"], "areas": {a["id"]: a["level"] for a in days["quiet"]["areas"]}}
    auto = publish.level_changes(prev, days["busy"]["areas"], days["busy"]["overall"]["level"], now)
    days["busy"]["history"] = sorted(list(history) + auto, key=lambda e: e["date"], reverse=True)[:12]
    for f in days.values():
        f["mode"] = "live"
    return [days["quiet"], days["busy"]]


HANDSOFF_BAR = """
<style>
body{padding-bottom:110px}
#lvlbar{position:fixed;left:0;right:0;bottom:0;z-index:50;background:#000;color:#fff;border-top:4px solid #8FCBEA;padding:10px 12px calc(10px + env(safe-area-inset-bottom,0px));font:14px/1.3 system-ui,sans-serif}
#lvlbar p{margin:0 0 8px;color:#c7ced2}
#lvlbar .row{display:flex;gap:6px;flex-wrap:wrap}
#lvlbar button{flex:1 1 160px;font:700 15px/1.1 system-ui,sans-serif;padding:12px 8px;border:3px solid #fff;background:#000;color:#fff;cursor:pointer}
#lvlbar button[aria-pressed="true"]{background:#F5B301;color:#000;border-color:#F5B301}
#lvlbar button:focus-visible{outline:4px solid #8FCBEA;outline-offset:2px}
</style>
<div id="lvlbar" role="group" aria-label="Preview the fully automatic site">
  <p>Hands-off preview: written signals cleared, BBC strip switched on. Illustrative days run through the real rules, not real events.</p>
  <div class="row"><button type="button" data-l="1">Quiet day (MI5 at SUBSTANTIAL)</button><button type="button" data-l="2">Busy day (MI5 at SEVERE)</button></div>
</div>
<script>
(function(){
  var F=window.THW_PREVIEW.feeds, btns=document.querySelectorAll('#lvlbar button[data-l]');
  function go(n){ window.__thw.setFeed(F[n-1]); btns.forEach(function(b){ b.setAttribute('aria-pressed',String(+b.getAttribute('data-l')===n)); }); try{ history.replaceState(null,'','#day='+n); }catch(e){} window.scrollTo(0,0); }
  btns.forEach(function(b){ b.addEventListener('click',function(){ go(+b.getAttribute('data-l')); }); });
  document.addEventListener('keydown',function(e){ if(e.target.closest&&e.target.closest('dialog')) return; if(/^[12]$/.test(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.altKey) go(+e.key); });
  var m=location.hash.match(/day=([12])/);
  window.__thw.ready.then(function(){ go(m?+m[1]:2); });
})();
</script>
"""


def scenario_feeds(snap):
    feeds = []
    for lvl in range(1, 6):
        f = json.loads(json.dumps(snap))
        f["mode"] = "live"
        f["generated_at"] = publish.iso(publish.now_utc())
        f.pop("banner", None)
        if lvl == 3:
            f["official"]["terror_level"], f["official"]["live_checked"] = "SEVERE", True
        if lvl != 3:
            terror, notice, areas = SCEN[lvl]
            for a in f["areas"]:
                a["level"], a["status"] = areas[a["id"]]
                a["reason"] = "Illustrative scenario for checking the design. " + a["status"]
            f["notice"] = notice
            if lvl == 4:   # illustrative evidence lists, so the design can be checked
                ev = lambda t, d: {"title": "Illustrative: " + t, "source": "GOV.UK: Ministry of Defence", "date": d, "url": "https://www.gov.uk/"}
                for a in f["areas"]:
                    if a["id"] in ("military", "supply"):
                        a["basis"] = "evidence"
                        a["evidence"] = [ev("Statement on hostile state activity affecting UK " + a["id"], "2026-09-18T09:00:00Z"), ev("Cabinet Office warning on state-linked interference", "2026-09-11T09:00:00Z")]
            f["official"]["terror_level"], f["official"]["live_checked"] = terror, True
        alerts, grid = CHECKS[lvl]
        now_iso = publish.iso(publish.now_utc())
        terror = f["official"]["terror_level"]
        f["checks"] = [
            {"id": "alerts", "name": "Emergency Alerts", "state": alerts[0], "text": alerts[1], "detail": alerts[2], "url": "https://www.gov.uk/alerts", "source": "GOV.UK", "checked": now_iso},
            {"id": "terror", "name": "Terrorism threat level", "state": {"LOW": "clear", "MODERATE": "clear", "SUBSTANTIAL": "notice", "SEVERE": "notice", "CRITICAL": "alert"}[terror], "text": terror,
             "detail": publish.TERROR_TEXT[terror], "url": "https://www.mi5.gov.uk/threats-and-advice/terrorism-threat-levels", "source": "MI5", "checked": now_iso},
            {"id": "grid", "name": "Electricity grid notices", "state": grid[0], "text": grid[1], "detail": grid[2], "url": "https://bmrs.elexon.co.uk/", "source": "Elexon BMRS", "checked": now_iso,
             "extra": {"text": "Gas notices: National Gas", "url": publish.GAS_URL}},
            {"id": "internet", "name": "UK internet", "state": INTERNET.get(lvl, CLEAR_NET)[0], "text": INTERNET.get(lvl, CLEAR_NET)[1], "detail": INTERNET.get(lvl, CLEAR_NET)[2],
             "url": "https://radar.cloudflare.com/outage-center", "source": "Cloudflare Radar", "checked": now_iso, "extra": {"text": "Cable map: TeleGeography", "url": "https://www.submarinecablemap.com/"}},
            {"id": "space", "name": "Space weather", "state": SPACE[lvl][0], "text": SPACE[lvl][1], "detail": SPACE[lvl][2], "url": publish.MET_SPACE_URL, "source": "Met Office", "checked": now_iso,
             "extra": {"text": "Levels from NOAA", "url": "https://www.swpc.noaa.gov/"}},
        ]
        if lvl >= 4:
            f["press"] = [{"title": "Illustrative headline: major incident declared as emergency services respond", "url": "https://www.bbc.co.uk/news", "published": now_iso, "source": "Example news outlet (illustrative)"}]
        top = max(a["level"] for a in f["areas"])
        f["overall"] = {"level": top, "name": NAMES[top], "drivers": [a["id"] for a in f["areas"] if a["level"] == top]}
        feeds.append(f)
    return feeds


PREVIEW_BAR = """
<style>
body{padding-bottom:96px}
#lvlbar{position:fixed;left:0;right:0;bottom:0;z-index:50;background:#000;color:#fff;border-top:4px solid #8FCBEA;padding:10px 12px calc(10px + env(safe-area-inset-bottom,0px));font:14px/1.3 system-ui,sans-serif}
#lvlbar p{margin:0 0 8px;color:#c7ced2}
#lvlbar .row{display:flex;gap:6px;flex-wrap:wrap}
#lvlbar button{flex:1 1 90px;font:700 15px/1.1 system-ui,sans-serif;padding:12px 8px;border:3px solid #fff;background:#000;color:#fff;cursor:pointer}
#lvlbar button[aria-pressed="true"]{background:#F5B301;color:#000;border-color:#F5B301}
#lvlbar #failbtn{flex:0 1 auto}
#lvlbar button:focus-visible{outline:4px solid #8FCBEA;outline-offset:2px}
</style>
<div id="lvlbar" role="group" aria-label="Preview a threat level">
  <p>Level preview. Illustrative scenarios for checking the design, not real events. Keys 1 to 5 also work.</p>
  <div class="row" style="margin-bottom:6px"><button type="button" id="failbtn" aria-pressed="false" style="flex:0 1 auto;padding:8px 12px;font-size:14px">Simulate a check that cannot be read</button></div>
  <div class="row">
    <button type="button" data-l="1">1 Routine</button><button type="button" data-l="2">2 Aware</button><button type="button" data-l="3">3 Elevated</button><button type="button" data-l="4">4 High</button><button type="button" data-l="5">5 Critical</button>
  </div>
</div>
<script>
(function(){
  var F=window.THW_PREVIEW.feeds, btns=document.querySelectorAll('#lvlbar button[data-l]'), cur=3, fail=false;
  function view(){
    var f=JSON.parse(JSON.stringify(F[cur-1]));
    if(fail){ f.checks[2]=Object.assign(f.checks[2],{state:'unknown',text:'Could not check',detail:'Check the source directly.'}); f.checks[0]=Object.assign(f.checks[0],{state:'unknown',text:'Could not confirm',detail:'Check GOV.UK now. This page could not read the current alerts.'}); }
    return f;
  }
  document.getElementById('failbtn').addEventListener('click',function(){ fail=!fail; this.setAttribute('aria-pressed',String(fail)); window.__thw.setFeed(view()); });
  function go(n){
    cur=n; window.__thw.setFeed(view());
    btns.forEach(function(b){ b.setAttribute('aria-pressed', String(+b.getAttribute('data-l')===n)); });
    try{ history.replaceState(null,'','#level='+n); }catch(e){}
    window.scrollTo(0,0);
  }
  btns.forEach(function(b){ b.addEventListener('click',function(){ go(+b.getAttribute('data-l')); }); });
  document.addEventListener('keydown',function(e){ if(e.target.closest&&e.target.closest('dialog')) return; if(/^[1-5]$/.test(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.altKey) go(+e.key); });
  var m=location.hash.match(/level=([1-5])/);
  window.__thw.ready.then(function(){ go(m?+m[1]:3); });
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--name", help="override the site name for this build (to compare candidate names)")
    ap.add_argument("--handsoff-preview", action="store_true", help="preview the fully automatic site: written signals cleared, BBC strip on (illustrative inputs, real rules)")
    ap.add_argument("--level-preview", action="store_true", help="add a bar that switches the page between all five levels (illustrative scenarios)")
    a = ap.parse_args()
    cfg = publish.load_or_exit(os.path.join(ROOT, "site.json"))
    if a.name:
        cfg["name"] = cfg["short_name"] = a.name
    baseline = publish.load_or_exit(os.path.join(ROOT, "editorial", "baseline.json"))
    signals = publish.load_or_exit(os.path.join(ROOT, "editorial", "signals.json"))["signals"]
    problems = publish.validate_editorial(signals, baseline)
    if problems:
        print("Editorial files have problems:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1
    history = (publish.load_or_exit(os.path.join(ROOT, "editorial", "history.json"), {}) or {}).get("entries", [])
    # The saved offline copy has no live data. It shows the editor's last confirmed terrorism level, plainly marked as not confirmed live.
    snap = publish.build_feed(baseline, signals, {}, {"terror": None, "terror_effective": baseline["official_terror_baseline"]["level"], "terror_live": False,
                                                      "terror_at": None, "items": [], "weather": {}, "used": [], "issues": []}, publish.now_utc(), history)
    snap["mode"] = "snapshot"
    if a.preview:
        snap["banner"] = "This is a mock-up with sample data. The live site refreshes from official sources every 30 minutes."

    e = lambda k: html.escape(cfg.get(k, ""), quote=True)
    contact = cfg.get("contact", "").strip()
    contact_line = ""
    if contact:
        href = f"mailto:{e('contact')}" if "@" in contact else e("contact")
        contact_line = f'<p>Corrections and questions: <a href="{href}" rel="noopener">{e("contact")}</a></p>'
    else:
        print("note: site.json has no contact. Add one so people can report errors.", file=sys.stderr)
    donate = cfg.get("donate_url", "").strip()
    donate_block = f'<p><a href="{e("donate_url")}" target="_blank" rel="noopener noreferrer">Help with running costs</a> (optional, and there is nothing to buy)</p>' if donate else ""
    site = cfg.get("site_url", "").strip().rstrip("/")
    host = site.replace("https://", "").replace("http://", "")
    never = "Wary never sends emails, texts or notifications, and never asks you to log in or pay. If a message claims to be from it, it is not."
    address_line = f"<p>{'The only address for this site is <strong>' + html.escape(host) + '</strong>. ' if host else ''}{html.escape(never).replace('Wary', html.escape(cfg.get('name', 'This site')))}</p>"
    og_block = ""
    if site:
        og = html.escape
        og_block = (f'<meta property="og:type" content="website"><meta property="og:title" content="{og(cfg["name"])}: {og(cfg.get("description", ""))}">'
                    f'<meta property="og:description" content="{og(cfg.get("tagline", ""))} No cookies, no ads, no tracking.">'
                    f'<meta property="og:url" content="{og(site)}/"><meta property="og:image" content="{og(site)}/social.png">'
                    f'<meta name="twitter:card" content="summary_large_image">')
    subs = {"{{OG_BLOCK}}": og_block, "{{ADDRESS_LINE}}": address_line, "{{NAME}}": e("name"), "{{SHORT_NAME}}": e("short_name"), "{{TAGLINE}}": e("tagline"), "{{DESCRIPTION}}": e("description"), "{{OWNER}}": e("owner"),
            "{{HOST}}": e("host"), "{{CONTACT_LINE}}": contact_line, "{{DONATE_BLOCK}}": donate_block}

    counter_url = cfg.get("counter_url", "").strip()
    counter_origin = ""
    if counter_url:
        m = re.match(r"https://[^/]+", counter_url)
        counter_origin = m.group(0) if m else ""      # only the origin goes in the security policy; never the path
        if not counter_origin:
            print(f"warning: counter_url '{counter_url}' does not look like a plain https address; the visit counter is switched off for this build", file=sys.stderr)
            counter_url = ""
    subs["{{COUNTER_URL}}"] = counter_url
    subs["{{COUNTER_CONNECT}}"] = (" " + counter_origin) if counter_origin else ""
    subs["{{COUNTER_NOTE}}"] = ("<li>This page counts visits with a single number: no cookie, no ID, and nothing that could tell one visit from "
        "another. Cloudflare, which runs that counter, sees the request reach its network the same way it does for any site, before our code "
        "runs and adds 1; it never receives anything else from this page.</li>") if counter_url else ""

    def fill(text):
        for k, v in subs.items():
            text = text.replace(k, v)
        return text

    out = os.path.join(ROOT, "site")
    handsoff = a.handsoff_preview
    preview_html = ""
    preview_data = ""
    if handsoff:
        hist = (publish.load_or_exit(os.path.join(ROOT, "editorial", "history.json"), {}) or {}).get("entries", [])
        preview_data = "<script>window.THW_PREVIEW=" + json.dumps({"feeds": handsoff_feeds(baseline, hist)}, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n"
        preview_html = HANDSOFF_BAR
    elif a.level_preview:
        preview_data = "<script>window.THW_PREVIEW=" + json.dumps({"feeds": scenario_feeds(snap)}, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n"
        preview_html = PREVIEW_BAR
    os.makedirs(out, exist_ok=True)
    page = fill(open(os.path.join(ROOT, "src", "index.src.html"), encoding="utf-8").read())
    page = page.replace("__SNAPSHOT__", json.dumps(snap, ensure_ascii=False).replace("</", "<\\/"))
    if a.level_preview or handsoff:
        page = page.replace('<script id="snapshot"', preview_data + '<script id="snapshot"', 1).replace("</body>", preview_html + "</body>", 1)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    for name in os.listdir(os.path.join(ROOT, "src")):
        if name == "index.src.html":
            continue
        src, dst = os.path.join(ROOT, "src", name), os.path.join(out, name)
        if name.endswith((".webmanifest", ".js")):
            with open(dst, "w", encoding="utf-8") as f:
                f.write(fill(open(src, encoding="utf-8").read()))
        else:
            shutil.copy(src, dst)
    # A security contact file (RFC 9116), only when there is a public contact address to point to. It is refreshed on every build.
    if "@" in contact and site:
        from datetime import timedelta
        wk = os.path.join(out, ".well-known")
        os.makedirs(wk, exist_ok=True)
        expires = (publish.now_utc() + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(wk, "security.txt"), "w", encoding="utf-8") as f:
            f.write(f"Contact: mailto:{contact}\nExpires: {expires}\nPreferred-Languages: en\nCanonical: {site}/.well-known/security.txt\n")
    print("built site/ for", cfg["name"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
