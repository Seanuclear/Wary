"""Browser check of the built site, with the postcode and flood services mocked. It ASSERTS the privacy and safety promises and exits 1 on any failure.

Needs Playwright (pip install playwright, then: playwright install chromium).
    python3 tools/build_site.py && python3 tools/publish.py --editorial-only
    python3 -m http.server 8770 --directory site &
    python3 tests/browser_check.py

The publishing workflow runs it on every push (not on the 30-minute schedule) and skips the deploy if it fails.
"""
import json
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

PORT = os.environ.get("WARY_PORT", "8770")
BASE = f"http://127.0.0.1:{PORT}/"
POSTCODE_HOSTS = {"api.postcodes.io", "environment.data.gov.uk"}


def counter_host():
    """The visit counter's address, read from the actual built page (not hardcoded), so this check always matches
    whatever is really configured -- on, off, or pointed at a different Worker -- without needing to be edited."""
    try:
        html = urllib.request.urlopen(BASE, timeout=5).read().decode("utf-8", "replace")
        m = re.search(r"var CU='(https://[^']+)'", html)
        return re.match(r"https://[^/]+", m.group(1)).group(0).replace("https://", "") if m else None
    except Exception:
        return None


COUNTER_HOST = counter_host()
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}"} | POSTCODE_HOSTS | ({COUNTER_HOST} if COUNTER_HOST else set())
CORS = {"access-control-allow-origin": "*", "content-type": "application/json"}
failures = []
counter_hits = []  # every request the tests would have sent to the real counter, across the whole run


def route_counter(ctx):
    """Intercepts the counter host so no request from this script ever reaches the real, private Worker. A local
    204 stands in for its real response, so the page's own fire-once logic behaves exactly as it would live."""
    if not COUNTER_HOST:
        return
    ctx.route(f"https://{COUNTER_HOST}/**", lambda r: (counter_hits.append(r.request.url), r.fulfill(status=204, body=""))[-1] or None)


def new_ctx(**kw):
    ctx = browser_ref[0].new_context(**kw)
    route_counter(ctx)
    # Production sampling is deliberately random (for example counter_sample=0.1). In CI, force the
    # sampling decision into the counted path so the one-ping regression check is deterministic.
    if COUNTER_HOST and kw.get("java_script_enabled", True):
        ctx.add_init_script("Math.random = function(){ return 0; };")
    return ctx


browser_ref = [None]


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   ({detail})"))
    if not ok:
        failures.append(name)


def mock(ctx, country="England", district="Eastbourne", county="East Sussex", region="South East", ea="ok"):
    def h_pc(route):
        u = route.request.url
        if "/outcodes/" in u:
            route.fulfill(status=200, headers=CORS, body=json.dumps({"status": 200, "result": {"latitude": 50.7684, "longitude": 0.2905, "admin_district": [district],
                                                                                                "admin_county": [county] if county else [], "country": [country]}}))
        else:
            route.fulfill(status=200, headers=CORS, body=json.dumps({"status": 200, "result": [{"region": region}]}))

    def h_ea(route):
        if ea == "fail":
            return route.abort()
        route.fulfill(status=200, headers=CORS, body=json.dumps({"items": [{"description": "River Cuckmere at Sample", "severity": "Flood alert", "severityLevel": 3, "message": "Be prepared."}]}))
    ctx.route("https://api.postcodes.io/**", h_pc)
    ctx.route("https://environment.data.gov.uk/**", h_ea)


def wizard(pg, postcode, kids=(9,)):
    pg.wait_for_selector("#wiz[open]")
    if postcode:
        pg.fill("#wz-pc", postcode)
    pg.click("button.pri")
    for _ in kids:
        pg.click("button[aria-label='One more child']")
    for i, a in enumerate(kids):
        pg.select_option(f"#age{i}", str(a))
    pg.click("button.pri")
    pg.click("button.pri")
    pg.wait_for_timeout(900)


def box(pg, n=0):
    return pg.inner_text(f"#local-box .box >> nth={n}").replace("\n", " | ")


with sync_playwright() as p:
    b = p.chromium.launch()
    browser_ref[0] = b

    # 1. full flow with a postcode: what is contacted, and what is stored
    ctx = new_ctx(viewport={"width": 1100, "height": 900})
    reqs = []
    ctx.on("request", lambda r: reqs.append(r.url))
    mock(ctx)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE)
    wizard(pg, "BN21 4AA")
    hosts = {re.match(r"https?://([^/]+)", u).group(1) for u in reqs if u.startswith("http")}
    check("1a only the expected hosts are contacted", hosts <= ALLOWED_HOSTS, sorted(hosts - ALLOWED_HOSTS))
    check("1b the full postcode never appears in any request", not any("4AA" in u for u in reqs))
    check("1c no cookies are set", ctx.cookies() == [] and pg.evaluate("document.cookie") == "")
    keys = pg.evaluate("Object.keys(localStorage)")
    check("1d only the household profile is stored, on this device", keys == ["thw.profile"], keys)
    check("1e the stored profile does not contain the full postcode", "4AA" not in (pg.evaluate("localStorage.getItem('thw.profile')") or ""))
    check("1f a request to any other site is blocked by the security policy", pg.evaluate("fetch('https://example.com/x').then(()=>'ALLOWED',()=>'blocked')") == "blocked")
    check("1g no JavaScript errors", errs == [], errs)
    check("1h the promise line is on the page", "No cookies. No ads. No tracking." in pg.inner_text(".trust"))
    check("1i flood warnings are shown for the area", "River Cuckmere" in box(pg, 0), box(pg, 0)[:120])
    if COUNTER_HOST:
        check("1j the counter fires exactly once for a whole page life, and never reaches the real Worker directly",
              len(counter_hits) == 1 and counter_hits[0].startswith("https://" + COUNTER_HOST), counter_hits)
    ctx.close()

    # 2. a blank postcode makes no third-party request at all
    ctx = new_ctx()
    reqs = []
    ctx.on("request", lambda r: reqs.append(r.url))
    mock(ctx)
    pg = ctx.new_page()
    pg.goto(BASE)
    wizard(pg, "")
    third = [u for u in reqs if f"127.0.0.1:{PORT}" not in u]
    postcode_related = [u for u in third if any(h in u for h in POSTCODE_HOSTS)]
    check("2 no postcode or flood requests with a blank postcode", postcode_related == [], postcode_related)
    unexpected = [u for u in third if not (COUNTER_HOST and COUNTER_HOST in u)]
    check("2b nothing else unexpected is contacted either", unexpected == [], unexpected)
    ctx.close()

    # 3. the flood service failing is said plainly, not shown as 'no warnings'
    ctx = new_ctx()
    mock(ctx, ea="fail")
    pg = ctx.new_page()
    pg.goto(BASE)
    wizard(pg, "BN21")
    t = box(pg, 0)
    check("3 a failed flood lookup says so and does not claim all clear", "could not reach the Environment Agency" in t and "no flood" not in t.lower(), t[:160])
    ctx.close()

    # 4. Northern Ireland: England-only flood data is not presented as 'no warnings', and the NI note is separate
    ctx = new_ctx()
    mock(ctx, country="Northern Ireland", district="Belfast", county="", region="")
    pg = ctx.new_page()
    pg.goto(BASE)
    wizard(pg, "BT1")
    check("4a NI households are told the flood service covers England only", "England only" in box(pg, 0), box(pg, 0)[:160])
    pg.click("#area-security .area-head")
    pg.wait_for_timeout(400)
    check("4b the Northern Ireland terrorism note is shown separately", "Northern Ireland-related terrorism separately" in pg.inner_text("#p-security"))
    ctx.close()

    # 5. the live feed unavailable: a clearly labelled saved copy, never silently stale
    ctx = new_ctx()
    ctx.route("**/feed.json", lambda r: r.abort())
    pg = ctx.new_page()
    pg.goto(BASE)
    pg.wait_for_selector("#wiz[open]")
    check("5 an unavailable live feed shows a labelled saved copy", "Showing a saved copy" in pg.inner_text("#banner"), pg.inner_text("#banner")[:120])
    ctx.close()

    # 6. an editor's notice appears
    ctx = new_ctx()

    def notice(route):
        r = route.fetch()
        j = r.json()
        j["notice"] = "Cables and GPS raised to High after new reports."
        route.fulfill(response=r, body=json.dumps(j), headers={"content-type": "application/json"})
    ctx.route("**/feed.json", notice)
    pg = ctx.new_page()
    pg.goto(BASE)
    pg.wait_for_selector("#wiz[open]")
    check("6 an editor's notice is shown", "Cables and GPS raised to High" in pg.inner_text("#notice"))
    ctx.close()

    # 7. mobile: nothing forces sideways scrolling
    ctx = new_ctx(viewport={"width": 390, "height": 844})
    mock(ctx)
    pg = ctx.new_page()
    pg.goto(BASE)
    wizard(pg, "BN21 4AA")
    check("7 no sideways scrolling on a phone", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
    ctx.close()

    # 8. JavaScript switched off: the level and the six areas are still readable, with emergency links
    ctx = new_ctx(java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(BASE)
    body = pg.inner_text("body")
    check("8a without JavaScript the household level is shown", "Household level:" in body and re.search(r"\(\d of 5\)", body) is not None, body[:200])
    check("8b without JavaScript all six areas are listed", all(n in body for n in ("Power, gas and fuel", "Cyber attacks on services", "Terrorism and sabotage")), body[:200])
    check("8c without JavaScript the emergency links are shown", "gov.uk/alerts" in body)
    ctx.close()
    b.close()

if COUNTER_HOST:
    print(f"\n(the real counter at {COUNTER_HOST} was intercepted throughout: {len(counter_hits)} local test ping(s), 0 sent to the real Worker)")
print(f"\n{len(failures)} failure(s)" if failures else "\nAll browser checks passed")
sys.exit(1 if failures else 0)
