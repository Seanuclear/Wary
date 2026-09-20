"""Offline tests for the public edition builder. Run: python3 -m unittest discover -s tests -v
Fixture headlines are INVENTED for testing."""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import timedelta
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publish  # noqa: E402


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://example.test/{abs(hash(t)) % 10**8}</link><description>{d}</description>"
                   f"<pubDate>{format_datetime(w)}</pubDate></item>" for t, d, w in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        n = publish.now_utc()
        w = lambda h: n - timedelta(hours=h)
        put = lambda name, text: open(os.path.join(self.tmp, name), "w").write(text)
        put("mi5-level.html", "<p>The current national threat level is SEVERE.</p><p>The threat to Northern Ireland from Northern Ireland-related terrorism is SUBSTANTIAL.</p>")
        put("gov-mod.atom", rss([("Defence Secretary statement on NATO missile defence exercise plans", "Statement.", w(5)),
                                 ("Ministry of Defence recruitment fair dates announced", "Jobs.", w(6))]))
        put("ncsc.xml", rss([("New guidance for small businesses", "Advice.", w(20))]))
        put("met-se.xml", rss([("Yellow warning of wind in London and South East England", "Valid.", w(1)), ("No warnings", "", w(1))]))
        put("met-UK.xml", rss([]))
        self.doc = publish.load_json(os.path.join(ROOT, "official_sources.json"))
        self.baseline = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        self.signals = publish.load_json(os.path.join(ROOT, "editorial", "signals.json"))["signals"]
        self.now = n
        self.official = publish.gather_official(self.doc, self.tmp, n)
        self.feed = publish.build_feed(self.baseline, self.signals, {}, self.official, n)

    def area(self, a):
        return next(x for x in self.feed["areas"] if x["id"] == a)

    def test_shipped_editorial_files_are_valid(self):
        self.assertEqual(publish.validate_editorial(self.signals, self.baseline), [])

    def test_mi5_level_read_and_used_as_floor(self):
        self.assertEqual(self.feed["official"]["terror_level"], "SEVERE")
        self.assertTrue(self.feed["official"]["live_checked"])
        self.assertGreaterEqual(self.area("security")["level"], 3)

    def test_official_headlines_never_change_a_level(self):
        for a in self.feed["areas"]:
            self.assertEqual(a["level"], max(a["baseline"], 3 if a["id"] == "security" else 1))

    def test_noise_filtered_and_relevant_kept(self):
        titles = " ".join(s["title"] for s in self.feed["signals"] if s["auto"])
        self.assertIn("NATO missile defence", titles)
        self.assertNotIn("recruitment", titles)

    def test_automatic_items_carry_only_the_sources_own_short_summary(self):
        for s in self.feed["signals"]:
            if s["auto"]:
                self.assertIn(s["summary"], ("Statement.", "Guidance published.", "Advice."))
                self.assertLessEqual(len(s["summary"]), 300)

    def test_only_official_sources_are_ever_fetched(self):
        ids = {s["id"] for s in self.doc["sources"]}
        self.assertFalse(ids & {"bbc-uk", "bbc-world", "guardian-uk", "guardian-world", "sky-uk", "sky-world"})
        for s in self.doc["sources"]:
            self.assertTrue(urlparse_host(s["url"]).endswith(("gov.uk", "mi5.gov.uk", "ncsc.gov.uk")), s["url"])

    def test_weather_keyed_by_region_and_no_warnings_row_dropped(self):
        self.assertEqual(len(self.feed["weather"]["se"]), 1)
        self.assertEqual(self.feed["weather"]["UK"], [])

    def test_unavailable_sources_are_reported_not_fatal(self):
        self.assertIn("GOV.UK: Home Office", self.feed["data_issues"])

    def test_level_five_never_automatic(self):
        self.assertTrue(all(a["level"] <= 4 for a in self.feed["areas"]))

    def test_notice_shows_then_expires(self):
        live = publish.build_feed(self.baseline, self.signals, {"text": "Raised.", "until": iso_in(self.now, 2)}, self.official, self.now)
        gone = publish.build_feed(self.baseline, self.signals, {"text": "Old.", "until": iso_in(self.now, -2)}, self.official, self.now)
        self.assertEqual(live["notice"], "Raised.")
        self.assertEqual(gone["notice"], "")


def iso_in(now, days):
    return (now + timedelta(days=days)).strftime("%Y-%m-%d")


def urlparse_host(u):
    from urllib.parse import urlparse
    return urlparse(u).netloc


class Editorial(unittest.TestCase):
    def setUp(self):
        self.baseline = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        self.good = dict(publish.load_json(os.path.join(ROOT, "editorial", "signals.json"))["signals"][0])

    def problems(self, **chg):
        s = dict(self.good, **chg)
        return publish.validate_editorial([s], self.baseline)

    def test_missing_link_is_rejected(self):
        self.assertTrue(any("link" in p for p in self.problems(url="")))

    def test_non_http_link_is_rejected(self):
        self.assertTrue(self.problems(url="javascript:alert(1)"))

    def test_overlong_summary_is_rejected(self):
        self.assertTrue(any("700" in p for p in self.problems(summary="word " * 200)))

    def test_bad_area_is_rejected(self):
        self.assertTrue(self.problems(cats=["weather"]))

    def test_bad_baseline_level_is_rejected(self):
        b = json.loads(json.dumps(self.baseline)); b["areas"]["energy"]["level"] = 9
        self.assertTrue(publish.validate_editorial([self.good], b))


class Checks(unittest.TestCase):
    """The Live checks must only ever say 'none' when they positively read 'none'."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.now = publish.now_utc()

    def put(self, name, text):
        with open(os.path.join(self.tmp, name), "w") as f:
            f.write(text)

    def test_alerts_clear_only_when_the_page_says_so(self):
        self.put("alerts.html", "<h1>Emergency Alerts</h1><p>There are no current alerts</p>")
        c = publish.check_alerts(self.tmp, self.now)
        self.assertEqual((c["state"], c["text"]), ("clear", "None current"))

    def test_alerts_unknown_if_wording_changes_or_an_alert_is_live(self):
        self.put("alerts.html", "<h1>Emergency Alerts</h1><p>Current alerts</p><ul><li>Flood in Sample Town</li></ul>")
        self.assertEqual(publish.check_alerts(self.tmp, self.now)["state"], "unknown")

    def test_alerts_unknown_if_the_page_cannot_be_fetched(self):
        self.assertEqual(publish.check_alerts(os.path.join(self.tmp, "nothing"), self.now)["state"], "unknown")

    def _rows(self, *pairs):
        return json.dumps({"data": [{"publishTime": (self.now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"), "warningText": w} for h, w in pairs]})

    def test_grid_clear_when_empty_or_only_old(self):
        self.put("grid.json", json.dumps({"data": []}))
        self.assertEqual(publish.check_grid(self.tmp, self.now)["state"], "clear")
        self.put("grid.json", self._rows((72, "ELECTRICITY MARGIN NOTICE")))
        self.assertEqual(publish.check_grid(self.tmp, self.now)["state"], "clear")

    def test_grid_notice_for_a_routine_recent_warning(self):
        self.put("grid.json", self._rows((3, "ELECTRICITY MARGIN NOTICE")))
        c = publish.check_grid(self.tmp, self.now)
        self.assertEqual(c["state"], "notice")
        self.assertIn("routine", c["detail"])

    def test_grid_alert_for_emergency_wording(self):
        self.put("grid.json", self._rows((1, "DEMAND CONTROL IMMINENT")))
        self.assertEqual(publish.check_grid(self.tmp, self.now)["state"], "alert")

    def test_grid_unknown_when_times_cannot_be_read(self):
        self.put("grid.json", json.dumps({"data": [{"warningText": "ELECTRICITY MARGIN NOTICE"}]}))
        self.assertEqual(publish.check_grid(self.tmp, self.now)["state"], "unknown")

    def test_grid_unknown_when_the_source_is_down(self):
        self.assertEqual(publish.check_grid(os.path.join(self.tmp, "nothing"), self.now)["state"], "unknown")

    def test_terror_states(self):
        base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        for lvl, want in [("MODERATE", "clear"), ("SUBSTANTIAL", "notice"), ("SEVERE", "notice"), ("CRITICAL", "alert")]:
            self.assertEqual(publish.check_terror({"terror": lvl}, base, self.now)["state"], want)
        c = publish.check_terror({"terror": None}, base, self.now)
        self.assertIn("live check is unavailable", c["detail"])

    def test_strip_order_and_editorial_only_build_has_none(self):
        base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        self.assertEqual(publish.build_checks({}, base, self.now), [])
        self.put("alerts.html", "no current alerts"); self.put("grid.json", json.dumps({"data": []}))
        off = {"terror": "SEVERE", "checks": {"alerts": publish.check_alerts(self.tmp, self.now), "grid": publish.check_grid(self.tmp, self.now)}}
        self.assertEqual([c["id"] for c in publish.build_checks(off, base, self.now)], ["alerts", "terror", "grid"])


class Mi5Guard(unittest.TestCase):
    """A change to MI5's page must never be able to invent a false Critical."""

    def test_contextual_reading(self):
        self.assertEqual(publish.read_terror_level(b"<p>The current national threat level is SEVERE.</p>"), ("SEVERE", True))

    def test_definitions_list_is_only_a_loose_match(self):
        page = b"<ul><li>CRITICAL means an attack is expected imminently</li><li>SEVERE means highly likely</li></ul>"
        level, contextual = publish.read_terror_level(page)
        self.assertEqual((level, contextual), ("CRITICAL", False))
        self.assertFalse(publish.accept_terror(level, contextual, "SEVERE"))

    def test_loose_match_can_only_confirm(self):
        self.assertTrue(publish.accept_terror("SEVERE", False, "SEVERE"))

    def test_big_jumps_are_refused_but_one_step_is_accepted(self):
        self.assertFalse(publish.accept_terror("LOW", True, "SEVERE"))
        self.assertTrue(publish.accept_terror("CRITICAL", True, "SEVERE"))
        self.assertTrue(publish.accept_terror("SUBSTANTIAL", True, "SEVERE"))

    def test_bad_reading_falls_back_and_is_reported(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "mi5-level.html"), "w") as f:
            f.write("<ul><li>CRITICAL means imminent</li></ul>")
        doc = {"sources": [{"id": "mi5-level", "name": "MI5", "kind": "mi5_level", "url": "https://x.test", "licence": "x"}], "met_regions": []}
        out = publish.gather_official(doc, tmp, publish.now_utc(), "SEVERE")
        self.assertIsNone(out["terror"])
        self.assertTrue(any("MI5" in i for i in out["issues"]))


class HistoryAndReview(unittest.TestCase):
    def setUp(self):
        self.base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        self.hist = publish.load_json(os.path.join(ROOT, "editorial", "history.json"))["entries"]

    def test_shipped_history_is_valid(self):
        self.assertEqual(publish.validate_history(self.hist), [])

    def test_bad_history_is_rejected(self):
        self.assertTrue(publish.validate_history([{"date": "nope", "level": 9, "title": "", "text": "x" * 500}]))

    def test_atom_feed_is_well_formed_and_escaped(self):
        import xml.etree.ElementTree as ET
        e = [{"date": "2026-10-03", "level": 4, "title": "Cables & GPS raised <High>", "text": "Because of \"reasons\" & more."}] + self.hist
        xml = publish.atom_feed(e, {"name": "Wary", "site_url": "https://wary.org.uk"}, publish.now_utc())
        root = ET.fromstring(xml)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        self.assertEqual(len(root.findall("a:entry", ns)), 2)
        self.assertIn("Cables & GPS raised <High>", root.find("a:entry/a:title", ns).text)

    def test_reviewed_date_is_the_latest_baseline_review(self):
        b = json.loads(json.dumps(self.base)); b["areas"]["energy"]["as_of"] = "2026-12-01"
        self.assertEqual(publish.reviewed_date(b), "2026-12-01")

    def test_feed_carries_history_and_reviewed(self):
        f = publish.build_feed(self.base, [], {}, {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}, publish.now_utc(), self.hist)
        self.assertEqual(f["reviewed"], publish.reviewed_date(self.base))
        self.assertEqual(len(f["history"]), len(self.hist))


class LowTouch(unittest.TestCase):
    """Automatic escalation: official triggers only, 72 hours, never level 5."""

    def setUp(self):
        self.now = publish.now_utc()
        self.base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))

    def item(self, title, hours=2, cats=("cyber",), source="NCSC"):
        return {"id": "x", "cats": list(cats), "title": title, "source": source, "url": "https://example.test/x",
                "date": (self.now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"), "background": False}

    def test_nationally_significant_cyber_incident_lifts_cyber_to_high(self):
        bumps, _, _ = publish.auto_rules([self.item("NCSC statement on a nationally significant incident")], {}, self.now)
        self.assertEqual(bumps["cyber"][0], 4)

    def test_routine_headline_does_nothing(self):
        bumps, notices, _ = publish.auto_rules([self.item("New guidance for small businesses")], {}, self.now)
        self.assertEqual((bumps, notices), ({}, []))

    def test_old_trigger_expires_after_72_hours(self):
        bumps, _, _ = publish.auto_rules([self.item("nationally significant incident", hours=80)], {}, self.now)
        self.assertEqual(bumps, {})

    def test_cobr_gives_a_notice_but_changes_no_level(self):
        bumps, notices, _ = publish.auto_rules([self.item("Prime Minister chairs COBR meeting", cats=("military",), source="GOV.UK: Cabinet Office")], {}, self.now)
        self.assertEqual(bumps, {})
        self.assertEqual(len(notices), 1)

    def test_grid_alert_lifts_energy_but_a_routine_notice_does_not(self):
        bumps, _, _ = publish.auto_rules([], {"grid": {"state": "alert"}}, self.now)
        self.assertEqual(bumps["energy"][0], 4)
        bumps, _, _ = publish.auto_rules([], {"grid": {"state": "notice"}}, self.now)
        self.assertEqual(bumps, {})

    def test_bump_shows_in_the_feed_with_a_reason_and_never_reaches_five(self):
        official = {"terror": None, "items": [], "weather": {}, "used": [], "issues": [],
                    "bumps": {"cyber": (4, "NCSC: x", "u")}, "auto_notices": [{"text": "COBR", "url": "u"}]}
        f = publish.build_feed(self.base, [], {}, official, self.now)
        cyber = next(a for a in f["areas"] if a["id"] == "cyber")
        self.assertEqual(cyber["level"], 4)
        self.assertIn("Raised automatically", cyber["reason"])
        self.assertTrue(all(a["level"] <= 4 for a in f["areas"]))
        self.assertEqual(f["auto_notices"][0]["text"], "COBR")

    def test_grid_word_emergency_alone_is_only_a_routine_notice(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "grid.json"), "w") as fh:
            json.dump({"data": [{"publishTime": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "warningText": "EMERGENCY INSTRUCTION FOR ONE GENERATOR"}]}, fh)
        self.assertEqual(publish.check_grid(tmp, self.now)["state"], "notice")

    def test_editorial_signals_age_into_background_by_themselves(self):
        sig = [{"id": "a", "cats": ["comms"], "tier": 2, "date": (self.now - timedelta(days=60)).strftime("%Y-%m-%d"), "source": "s",
                "title": "t", "summary": "x", "url": "https://x.test", "background": False},
               {"id": "b", "cats": ["comms"], "tier": 2, "date": (self.now - timedelta(days=5)).strftime("%Y-%m-%d"), "source": "s",
                "title": "t", "summary": "x", "url": "https://x.test", "background": False}]
        f = publish.build_feed(self.base, sig, {}, {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}, self.now)
        by = {s["id"]: s["background"] for s in f["signals"]}
        self.assertEqual(by, {"a": True, "b": False})

    def test_default_stale_window_is_sixty_days(self):
        f = publish.build_feed(self.base, [], {}, {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}, self.now)
        self.assertEqual(f["stale_days"], 60)


class LevelFive(unittest.TestCase):
    """Level 5 needs a clear official signal that has HELD for the hold time. Until then it shows High."""

    def setUp(self):
        self.now = publish.now_utc()

    def item(self, title, hours=1, cats=("security",), source="GOV.UK: Home Office"):
        return {"id": "x" + title[:4], "cats": list(cats), "title": title, "source": source, "url": "https://example.test/x",
                "date": (self.now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"), "background": False}

    def state_seen(self, key, hours_ago):
        return {"first_seen": {key: publish.iso(self.now - timedelta(hours=hours_ago))}}

    def test_critical_shows_high_on_first_sight(self):
        bumps, _, active = publish.auto_rules([], {}, self.now, terror="CRITICAL", state={"first_seen": {}})
        self.assertEqual(bumps["security"][0], 4)
        self.assertIn("mi5-critical", active)

    def test_critical_becomes_level_five_only_after_six_hours(self):
        early = publish.auto_rules([], {}, self.now, "CRITICAL", self.state_seen("mi5-critical", 5.9))[0]
        later = publish.auto_rules([], {}, self.now, "CRITICAL", self.state_seen("mi5-critical", 6.1))[0]
        self.assertEqual(early["security"][0], 4)
        self.assertEqual(later["security"][0], 5)

    def test_no_state_means_no_level_five(self):
        self.assertEqual(publish.auto_rules([], {}, self.now, "CRITICAL", None)[0]["security"][0], 4)

    def test_severe_never_triggers(self):
        bumps, _, active = publish.auto_rules([], {}, self.now, "SEVERE", self.state_seen("mi5-critical", 50))
        self.assertEqual((bumps, active), ({}, set()))

    def test_named_attack_alone_is_not_enough(self):
        bumps, _, active = publish.auto_rules([self.item("Statement on the attack on the UK")], {}, self.now)
        self.assertEqual((bumps, active), ({}, set()))

    def test_cobr_alone_is_not_enough(self):
        bumps, notices, active = publish.auto_rules([self.item("Prime Minister chairs COBR")], {}, self.now)
        self.assertEqual((bumps, len(notices), active), ({}, 1, set()))

    def test_attack_named_and_cobr_together_start_at_high_then_hold_to_five(self):
        items = [self.item("Home Secretary statement on the attack on the UK"), self.item("Prime Minister chairs COBR", source="GOV.UK: Cabinet Office", cats=("military",))]
        first = publish.auto_rules(items, {}, self.now, None, {"first_seen": {}})
        self.assertEqual(first[0]["security"][0], 4)
        self.assertIn("gov-attack", first[2])
        held = publish.auto_rules(items, {}, self.now, None, self.state_seen("gov-attack", 7))
        self.assertEqual(held[0]["security"][0], 5)

    def test_foreign_attack_headline_does_not_match(self):
        bumps, _, _ = publish.auto_rules([self.item("British nationals affected by attack in Paris"), self.item("COBR meets")], {}, self.now)
        self.assertEqual(bumps, {})

    def test_state_timer_restarts_if_the_trigger_disappears(self):
        st = self.state_seen("mi5-critical", 10)
        gone = publish.update_state(st, set(), self.now)
        self.assertEqual(gone["first_seen"], {})
        back = publish.update_state(gone, {"mi5-critical"}, self.now)
        self.assertFalse(publish.held(back, "mi5-critical", self.now, 6))

    def test_update_state_keeps_the_original_first_seen(self):
        st = self.state_seen("mi5-critical", 3)
        new = publish.update_state(st, {"mi5-critical"}, self.now)
        self.assertEqual(new["first_seen"]["mi5-critical"], st["first_seen"]["mi5-critical"])

    def test_feed_reaches_level_five_with_a_reason(self):
        base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        off = {"terror": "CRITICAL", "items": [], "weather": {}, "used": [], "issues": [],
               "bumps": {"security": (5, "the official terrorism threat level is CRITICAL and has held for over 6 hours", "u")}, "auto_notices": []}
        f = publish.build_feed(base, [], {}, off, self.now)
        self.assertEqual(f["overall"]["level"], 5)
        sec = next(a for a in f["areas"] if a["id"] == "security")
        self.assertIn("Raised automatically to Critical", sec["reason"])

    def test_official_headlines_carry_the_sources_own_summary(self):
        base = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        off = {"terror": None, "items": [{"id": "o1", "cats": ["cyber"], "title": "T", "summary": "The department's own description.", "source": "GOV.UK", "date": publish.iso(self.now), "url": "https://x.test", "background": False}],
               "weather": {}, "used": [], "issues": []}
        f = publish.build_feed(base, [], {}, off, self.now)
        self.assertEqual(f["signals"][0]["summary"], "The department's own description.")


class EmergencyBrake(unittest.TestCase):
    def test_auto_levels_false_removes_all_automatic_raising(self):
        import subprocess
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "mi5-level.html"), "w") as f:
            f.write("<p>The current national threat level is CRITICAL.</p>")
        for n in ("gov-mod.atom", "gov-homeoffice.atom", "gov-cabinet.atom", "gov-desnz.atom", "ncsc.xml"):
            with open(os.path.join(tmp, n), "w") as f:
                f.write('<rss version="2.0"><channel></channel></rss>')
        out = os.path.join(tmp, "site", "feed.json")
        state = os.path.join(tmp, "state.json")
        with open(state, "w") as f:
            json.dump({"first_seen": {"mi5-critical": publish.iso(publish.now_utc() - timedelta(hours=9))}}, f)
        cfg_path = os.path.join(ROOT, "site.json")
        original = open(cfg_path).read()
        try:
            def run(flag):
                cfg = json.loads(original); cfg["auto_levels"] = flag
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f)
                subprocess.run([sys.executable, os.path.join(ROOT, "tools", "publish.py"), "--fixtures", tmp, "--state", state, "--out", out], check=True, capture_output=True)
                return json.load(open(out))["overall"]["level"]
            self.assertEqual(run(True), 5)
            self.assertEqual(run(False), 4)   # the official CRITICAL floor still shows High, but nothing automatic reaches 5
        finally:
            with open(cfg_path, "w") as f:
                f.write(original)


class LevelPreview(unittest.TestCase):
    def test_five_scenarios_cover_levels_one_to_five(self):
        import build_site
        baseline = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
        signals = publish.load_json(os.path.join(ROOT, "editorial", "signals.json"))["signals"]
        snap = publish.build_feed(baseline, signals, {}, {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}, publish.now_utc())
        feeds = build_site.scenario_feeds(snap)
        self.assertEqual([f["overall"]["level"] for f in feeds], [1, 2, 3, 4, 5])
        self.assertEqual([f["overall"]["name"] for f in feeds], ["Routine", "Aware", "Elevated", "High", "Critical"])
        self.assertTrue(all(publish.LEVEL_NAMES[f["overall"]["level"]] == f["overall"]["name"] for f in feeds))


class BuiltSite(unittest.TestCase):
    """The promise on the page is 'no cookies, no ads, no tracking'. These checks keep the built page honest."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_site.py")], check=True, capture_output=True)
        cls.page = open(os.path.join(ROOT, "site", "index.html"), encoding="utf-8").read()

    def test_no_third_party_scripts_styles_or_fonts(self):
        self.assertNotRegex(self.page, r"<script[^>]+\ssrc=")
        self.assertNotRegex(self.page, r"<link[^>]+rel=[\"']stylesheet")
        self.assertNotIn("googleapis", self.page)
        self.assertNotIn("gstatic", self.page)

    def test_csp_only_allows_the_two_lookup_services(self):
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', self.page).group(1)
        self.assertIn("default-src 'none'", csp)
        hosts = set(re.findall(r"https://[a-z.]+", csp))
        self.assertEqual(hosts, {"https://api.postcodes.io", "https://environment.data.gov.uk"})

    def test_no_cookie_writes_in_code(self):
        self.assertNotIn("document.cookie", self.page)

    def test_no_unfilled_placeholders(self):
        self.assertNotIn("{{", self.page)
        self.assertNotIn("__SNAPSHOT__", self.page)

    def test_tagline_present(self):
        self.assertIn("No cookies. No ads. No tracking.", self.page)


if __name__ == "__main__":
    unittest.main()
