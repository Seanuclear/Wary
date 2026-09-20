"""Optional browser check of the built site, with the postcode and flood services mocked.
Needs Playwright (pip install playwright, then: playwright install chromium).
    python3 tools/build_site.py && python3 tools/publish.py --editorial-only
    python3 -m http.server 8770 --directory site &     then     python3 tests/browser_check.py
It prints what the page contacted, so you can confirm the privacy claims for yourself."""
import json, re
from playwright.sync_api import sync_playwright
CORS={"access-control-allow-origin":"*","content-type":"application/json"}
def mock(ctx, country="England", district="Eastbourne", county="East Sussex", region="South East", ea="ok", log=None):
    def h_pc(route):
        u=route.request.url
        if log is not None: log.append(u)
        if "/outcodes/" in u:
            route.fulfill(status=200,headers=CORS,body=json.dumps({"status":200,"result":{"latitude":50.7684,"longitude":0.2905,"admin_district":[district],"admin_county":[county] if county else [],"country":[country]}}))
        else:
            route.fulfill(status=200,headers=CORS,body=json.dumps({"status":200,"result":[{"region":region}]}))
    def h_ea(route):
        if log is not None: log.append(route.request.url)
        if ea=="fail": return route.abort()
        route.fulfill(status=200,headers=CORS,body=json.dumps({"items":[{"description":"River Cuckmere at Sample","severity":"Flood alert","severityLevel":3,"message":"Be prepared."},{"description":"Old","severityLevel":4}]}))
    ctx.route("https://api.postcodes.io/**",h_pc); ctx.route("https://environment.data.gov.uk/**",h_ea)

def wizard(pg, postcode, adults_more=0, kids=(9,)):
    pg.wait_for_selector("#wiz[open]")
    if postcode: pg.fill("#wz-pc",postcode)
    pg.click("button.pri")
    for _ in range(adults_more): pg.click("button[aria-label='One more adult']")
    for a in kids:
        pg.click("button[aria-label='One more child']")
    for i,a in enumerate(kids): pg.select_option(f"#age{i}",str(a))
    pg.click("button.pri"); pg.click("button.pri"); pg.wait_for_timeout(900)

with sync_playwright() as p:
    b=p.chromium.launch()
    # 1. full flow with postcode
    ctx=b.new_context(viewport={"width":1100,"height":900}); reqs=[]; ctx.on("request",lambda r:reqs.append(r.url)); log=[]
    mock(ctx,log=log); pg=ctx.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
    pg.goto("http://127.0.0.1:8770/"); wizard(pg,"BN21 4AA")
    print("1 tagline:", pg.inner_text(".trust"))
    print("1 status:", pg.inner_text("#status-text"))
    print("1 chip:", pg.inner_text("#hh-text"))
    print("1 flood box:", pg.inner_text("#local-box .box >> nth=0").replace("\n"," | ")[:230])
    print("1 weather box:", pg.inner_text("#local-box .box >> nth=1").replace("\n"," | ")[:230])
    hosts=sorted({re.match(r"https?://([^/]+)",u).group(1) for u in reqs if u.startswith("http")})
    print("1 hosts contacted:", hosts)
    print("1 postcode leak check (4AA in any request):", any("4AA" in u for u in reqs))
    print("1 cookies:", ctx.cookies(), "| document.cookie:", repr(pg.evaluate("document.cookie")))
    print("1 localStorage keys:", pg.evaluate("Object.keys(localStorage)"))
    blocked=pg.evaluate("fetch('https://example.com/x').then(()=>'ALLOWED',()=>'blocked by CSP')")
    print("1 fetch to other host:", blocked)
    print("1 errors:", errs)
    pg.screenshot(path="/tmp/pub-desk-top.png",clip={"x":0,"y":0,"width":1100,"height":700})
    ctx.close()
    # 2. blank postcode: no third-party calls
    ctx=b.new_context(viewport={"width":1100,"height":900}); reqs=[]; ctx.on("request",lambda r:reqs.append(r.url)); mock(ctx); pg=ctx.new_page()
    pg.goto("http://127.0.0.1:8770/"); wizard(pg,"")
    print("2 third-party requests with blank postcode:", [u for u in reqs if "127.0.0.1" not in u])
    print("2 local box:", pg.inner_text("#local-box .box >> nth=0").replace("\n"," | ")[:160])
    ctx.close()
    # 3. EA failure
    ctx=b.new_context(); mock(ctx,ea="fail"); pg=ctx.new_page(); pg.goto("http://127.0.0.1:8770/"); wizard(pg,"BN21")
    print("3 EA fail message:", pg.inner_text("#local-box .box >> nth=0").replace("\n"," | ")[:200]); ctx.close()
    # 4. Northern Ireland
    ctx=b.new_context(); mock(ctx,country="Northern Ireland",district="Belfast",county="",region=""); pg=ctx.new_page(); pg.goto("http://127.0.0.1:8770/"); wizard(pg,"BT1")
    print("4 NI flood box:", pg.inner_text("#local-box .box >> nth=0").replace("\n"," | ")[:220])
    pg.click("#area-security .area-head"); pg.wait_for_timeout(400)
    print("4 NI security note present:", "Northern Ireland-related terrorism separately" in pg.inner_text("#p-security"))
    print("4 weather box:", pg.inner_text("#local-box .box >> nth=1").replace("\n"," | ")[:160]); ctx.close()
    # 5. feed.json unavailable -> saved copy
    ctx=b.new_context(); ctx.route("**/feed.json",lambda r:r.abort()); pg=ctx.new_page(); pg.goto("http://127.0.0.1:8770/"); pg.wait_for_selector("#wiz[open]")
    print("5 offline banner:", pg.inner_text("#banner").strip()[:120], "| status:", pg.inner_text("#status-text")); ctx.close()
    # 6. editor notice
    ctx=b.new_context()
    def notice(route):
        r=route.fetch(); j=r.json(); j["notice"]="Cables and GPS raised to High on 3 Oct after new reports."; route.fulfill(response=r,body=json.dumps(j),headers={"content-type":"application/json"})
    ctx.route("**/feed.json",notice); pg=ctx.new_page(); pg.goto("http://127.0.0.1:8770/"); pg.wait_for_selector("#wiz[open]")
    print("6 notice:", pg.inner_text("#notice").strip()); ctx.close()
    # 7. mobile screenshots
    ctx=b.new_context(viewport={"width":390,"height":844}); mock(ctx); pg=ctx.new_page(); pg.goto("http://127.0.0.1:8770/"); wizard(pg,"BN21 4AA")
    pg.screenshot(path="/tmp/pub-mob-top.png",clip={"x":0,"y":0,"width":390,"height":844})
    el=pg.query_selector("#local"); pg.evaluate("window.scrollTo(0,document.querySelector('#local').offsetTop-10)"); pg.wait_for_timeout(200)
    pg.screenshot(path="/tmp/pub-mob-local.png")
    pg.evaluate("window.scrollTo(0,document.querySelector('#about').offsetTop-10)"); pg.wait_for_timeout(200); pg.screenshot(path="/tmp/pub-mob-about.png")
    ctx.close(); b.close()
