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


def fresh_baseline():
    """The shipped baseline with every review date set to today, so tests never depend on the calendar."""
    b = publish.load_json(os.path.join(ROOT, "editorial", "baseline.json"))
    today = publish.now_utc().strftime("%Y-%m-%d")
    for a in b["areas"].values():
        a["as_of"] = today
    return b


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
        self.baseline = fresh_baseline()
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
        self.baseline = fresh_baseline()
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
        base = fresh_baseline()
        for lvl, want in [("MODERATE", "clear"), ("SUBSTANTIAL", "notice"), ("SEVERE", "notice"), ("CRITICAL", "alert")]:
            self.assertEqual(publish.check_terror({"terror": lvl}, base, self.now)["state"], want)
        c = publish.check_terror({"terror": None}, base, self.now)
        self.assertIn("live check is unavailable", c["detail"])

    def test_strip_order_and_editorial_only_build_has_none(self):
        base = fresh_baseline()
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
        self.assertFalse(publish.accept_terror(level, contextual, "SEVERE")[0])

    def test_loose_match_can_only_confirm(self):
        self.assertTrue(publish.accept_terror("SEVERE", False, "SEVERE")[0])

    def test_big_jumps_wait_for_the_hold_but_one_step_is_accepted(self):
        now = publish.now_utc()
        self.assertFalse(publish.accept_terror("LOW", True, "SEVERE", {"first_seen": {}}, now)[0])
        self.assertEqual(publish.accept_terror("LOW", True, "SEVERE", {"first_seen": {}}, now)[1], "terror-jump:LOW")
        seen = {"first_seen": {"terror-jump:LOW": publish.iso(now - timedelta(hours=7))}}
        self.assertTrue(publish.accept_terror("LOW", True, "SEVERE", seen, now)[0])
        self.assertTrue(publish.accept_terror("CRITICAL", True, "SEVERE")[0])
        self.assertTrue(publish.accept_terror("SUBSTANTIAL", True, "SEVERE")[0])

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
        self.base = fresh_baseline()
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


class SpaceWeather(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.now = publish.now_utc()

    def put(self, obj):
        with open(os.path.join(self.tmp, "space.json"), "w") as f:
            json.dump(obj, f)

    def scales(self, latest, day_max, forecast):
        return {"-1": {"G": {"Scale": str(latest), "Text": "x"}}, "0": {"G": {"Scale": str(day_max), "Text": "x"}}, "1": {"G": {"Scale": str(forecast), "Text": "x"}}}

    def test_quiet_is_clear_only_when_a_value_was_read(self):
        self.put(self.scales(0, 1, 2))
        c = publish.check_space(self.tmp, self.now)
        self.assertEqual((c["state"], c["text"]), ("clear", "Quiet to moderate"))

    def test_strong_storm_forecast_is_a_notice(self):
        self.put(self.scales(1, 1, 3))
        c = publish.check_space(self.tmp, self.now)
        self.assertEqual(c["state"], "notice")
        self.assertIn("G3", c["text"])
        self.assertIn("before assuming a hostile cause", c["detail"])

    def test_extreme_storm_is_an_alert(self):
        self.put(self.scales(5, 5, 4))
        self.assertEqual(publish.check_space(self.tmp, self.now)["state"], "alert")

    def test_unreadable_values_are_unknown_not_clear(self):
        self.put({"-1": {"G": {"Scale": None}}, "0": {}, "1": {"G": {"Scale": "n/a"}}})
        self.assertEqual(publish.check_space(self.tmp, self.now)["state"], "unknown")

    def test_source_down_is_unknown(self):
        self.assertEqual(publish.check_space(os.path.join(self.tmp, "nothing"), self.now)["state"], "unknown")

    def test_space_weather_never_changes_a_level(self):
        official = {"terror": None, "items": [], "weather": {}, "used": [], "issues": [], "checks": {}}
        base = fresh_baseline()
        self.put(self.scales(5, 5, 5))
        official["checks"] = {"alerts": {"id": "alerts", "state": "clear"}, "grid": {"id": "grid", "state": "clear"}, "space": publish.check_space(self.tmp, self.now)}
        bumps, _, _ = publish.auto_rules([], official["checks"], self.now)
        self.assertEqual(bumps, {})
        strip = publish.build_checks(official, base, self.now)
        self.assertEqual([c["id"] for c in strip], ["alerts", "terror", "grid", "space"])


class LowTouch(unittest.TestCase):
    """Automatic escalation: official triggers only, 72 hours, never level 5."""

    def setUp(self):
        self.now = publish.now_utc()
        self.base = fresh_baseline()

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
        base = fresh_baseline()
        off = {"terror": "CRITICAL", "items": [], "weather": {}, "used": [], "issues": [],
               "bumps": {"security": (5, "the official terrorism threat level is CRITICAL and has held for over 6 hours", "u")}, "auto_notices": []}
        f = publish.build_feed(base, [], {}, off, self.now)
        self.assertEqual(f["overall"]["level"], 5)
        sec = next(a for a in f["areas"] if a["id"] == "security")
        self.assertIn("Raised automatically to Critical", sec["reason"])

    def test_official_headlines_carry_the_sources_own_summary(self):
        base = fresh_baseline()
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


class FallBack(unittest.TestCase):
    """Levels can come back down without the editor: terrorism follows MI5, other areas settle if nobody reviews them."""

    def setUp(self):
        self.now = publish.now_utc()
        self.base = fresh_baseline()
        self.empty = {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}

    def feed(self, terror=None, base=None, **kw):
        off = dict(self.empty, terror=terror)
        return publish.build_feed(base or self.base, [], {}, off, self.now, **kw)

    def area(self, f, a):
        return next(x for x in f["areas"] if x["id"] == a)

    def test_terrorism_follows_mi5_down(self):
        f = self.feed("SUBSTANTIAL")
        sec = self.area(f, "security")
        self.assertEqual((sec["level"], sec["basis"]), (2, "official"))
        self.assertIn("SUBSTANTIAL", sec["status"])
        self.assertEqual(self.area(self.feed("MODERATE"), "security")["level"], 1)

    def test_terrorism_follows_mi5_up(self):
        b = json.loads(json.dumps(self.base)); b["areas"]["security"]["level"] = 2
        self.assertEqual(self.area(self.feed("SEVERE", b), "security")["level"], 3)

    def test_unreadable_mi5_falls_back_to_the_editors_level(self):
        sec = self.area(self.feed(None), "security")
        self.assertEqual((sec["level"], sec["basis"]), (self.base["areas"]["security"]["level"], "editor"))
        self.assertIn("not available", sec["reason"])

    def test_status_text_never_states_a_stale_level(self):
        sec = self.area(self.feed("MODERATE"), "security")
        self.assertNotIn("SEVERE", sec["status"])

    def test_unreviewed_areas_settle_at_aware_after_the_decay_window(self):
        old = json.loads(json.dumps(self.base))
        old_date = (self.now - timedelta(days=120)).strftime("%Y-%m-%d")
        for a in old["areas"].values():
            a["as_of"] = old_date
        f = self.feed("SEVERE", old, decay_days=90)
        cyber = self.area(f, "cyber")
        self.assertEqual((cyber["level"], cyber["lowered"], cyber["basis"]), (2, True, "decayed"))
        self.assertIn("Lowered automatically", cyber["reason"])
        self.assertEqual(self.area(f, "security")["level"], 3)     # terrorism does not decay: it follows MI5

    def test_recently_reviewed_areas_do_not_decay(self):
        f = self.feed("SEVERE")
        self.assertEqual(self.area(f, "cyber")["level"], self.base["areas"]["cyber"]["level"])
        self.assertFalse(self.area(f, "cyber")["lowered"])

    def test_decay_never_goes_below_aware_and_never_touches_low_levels(self):
        old = json.loads(json.dumps(self.base)); old["areas"]["supply"]["level"] = 1
        for a in old["areas"].values():
            a["as_of"] = (self.now - timedelta(days=400)).strftime("%Y-%m-%d")
        f = self.feed("SEVERE", old)
        self.assertEqual(self.area(f, "supply")["level"], 1)
        self.assertTrue(all(a["level"] >= 1 for a in f["areas"]))
        self.assertTrue(all(a["level"] <= 3 for a in f["areas"]))

    def test_an_official_trigger_still_beats_the_decay(self):
        old = json.loads(json.dumps(self.base))
        for a in old["areas"].values():
            a["as_of"] = (self.now - timedelta(days=400)).strftime("%Y-%m-%d")
        off = dict(self.empty, terror="SEVERE", bumps={"comms": (4, "MoD: x", "u")})
        f = publish.build_feed(old, [], {}, off, self.now)
        self.assertEqual(self.area(f, "comms")["level"], 4)

    def test_an_area_lifted_by_a_trigger_is_labelled_raised_not_lowered(self):
        old = json.loads(json.dumps(self.base))
        for a in old["areas"].values():
            a["as_of"] = (self.now - timedelta(days=400)).strftime("%Y-%m-%d")
        f = publish.build_feed(old, [], {}, dict(self.empty, terror="SEVERE", bumps={"comms": (4, "MoD: x", "u")}), self.now)
        self.assertEqual(self.area(f, "comms")["basis"], "raised")

    def test_feed_reports_the_decay_window(self):
        self.assertEqual(self.feed("SEVERE")["decay_days"], 90)

    def test_last_accepted_mi5_level_is_remembered(self):
        st = publish.update_state({"first_seen": {}}, set(), self.now, "SUBSTANTIAL")
        self.assertEqual(st["terror_last"], "SUBSTANTIAL")
        self.assertEqual(publish.update_state(st, set(), self.now)["terror_last"], "SUBSTANTIAL")


class Cables(unittest.TestCase):
    def setUp(self):
        self.now = publish.now_utc()
        self.tmp = tempfile.mkdtemp()

    def item(self, title, hours=2):
        return {"id": "c1", "cats": ["comms"], "title": title, "source": "GOV.UK: Ministry of Defence", "url": "https://example.test/x",
                "date": (self.now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"), "background": False}

    def test_official_statement_about_damage_to_a_cable_lifts_comms(self):
        bumps, _, _ = publish.auto_rules([self.item("Ministry of Defence statement on damage to an undersea cable")], {}, self.now)
        self.assertEqual(bumps["comms"][0], 4)

    def test_cable_headline_without_damage_does_nothing(self):
        for title in ("Undersea cable protection strategy published", "Statement on Russian submarine activity near UK infrastructure"):
            self.assertEqual(publish.auto_rules([self.item(title)], {}, self.now)[0], {}, title)

    def test_old_cable_statement_expires(self):
        self.assertEqual(publish.auto_rules([self.item("Damage to undersea cable", hours=80)], {}, self.now)[0], {})

    def put(self, obj):
        with open(os.path.join(self.tmp, "internet.json"), "w") as f:
            json.dump(obj, f)

    def ann(self, hours_ago, cause="CABLE_CUT", ended=None, desc="Traffic drop in the UK."):
        d = {"startDate": publish.iso(self.now - timedelta(hours=hours_ago)), "endDate": ended, "description": desc, "outage": {"outageCause": cause, "outageType": "NETWORK"}}
        return d

    def test_internet_tile_is_absent_without_a_token(self):
        self.assertIsNone(publish.check_internet(None, self.now, ""))

    def test_internet_clear_only_when_a_list_was_read(self):
        self.put({"success": True, "result": {"annotations": []}})
        c = publish.check_internet(self.tmp, self.now, "x")
        self.assertEqual((c["state"], c["text"]), ("clear", "None in 24 hours"))

    def test_recent_cable_cut_is_an_informational_notice(self):
        self.put({"success": True, "result": {"annotations": [self.ann(3)]}})
        c = publish.check_internet(self.tmp, self.now, "x")
        self.assertEqual(c["state"], "notice")
        self.assertIn("cable damage", c["text"])

    def test_old_or_finished_events_are_ignored(self):
        self.put({"success": True, "result": {"annotations": [self.ann(100), self.ann(30, ended=publish.iso(self.now - timedelta(hours=26)))]}})
        self.assertEqual(publish.check_internet(self.tmp, self.now, "x")["state"], "clear")

    def test_bad_response_is_unknown_not_clear(self):
        self.put({"success": False, "errors": [{"message": "bad token"}]})
        self.assertEqual(publish.check_internet(self.tmp, self.now, "x")["state"], "unknown")
        self.put({"result": {}})
        self.assertEqual(publish.check_internet(self.tmp, self.now, "x")["state"], "unknown")

    def test_internet_tile_never_changes_a_level(self):
        checks = {"grid": {"state": "clear"}, "internet": publish.check_internet(self.tmp, self.now, "x")}
        self.put({"success": True, "result": {"annotations": [self.ann(1)]}})
        checks["internet"] = publish.check_internet(self.tmp, self.now, "x")
        self.assertEqual(publish.auto_rules([], checks, self.now)[0], {})


class SignalUpkeep(unittest.TestCase):
    """Signals look after themselves: dead links retire them, and very old ones fall away."""

    def setUp(self):
        self.now = publish.now_utc()
        self.base = fresh_baseline()
        self.empty = {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}

    def sig(self, sid, url="https://example.test/a", days=5, **kw):
        d = {"id": sid, "cats": ["comms"], "tier": 2, "date": (self.now - timedelta(days=days)).strftime("%Y-%m-%d"), "source": "s",
             "title": "t", "summary": "x", "url": url, "background": False}
        d.update(kw)
        return d

    def test_one_404_does_not_retire_but_two_separate_checks_do(self):
        s = [self.sig("a")]
        links, gone = publish.check_links(s, {}, self.now, lambda u: 404)
        self.assertEqual(gone, set())
        later = self.now + timedelta(hours=25)
        links2, gone2 = publish.check_links(s, {"links": links}, later, lambda u: 404)
        self.assertEqual(gone2, {"a"})

    def test_a_working_link_resets_the_count(self):
        s = [self.sig("a")]
        links, _ = publish.check_links(s, {}, self.now, lambda u: 404)
        links, _ = publish.check_links(s, {"links": links}, self.now + timedelta(hours=25), lambda u: 200)
        self.assertEqual(links["https://example.test/a"]["fails"], 0)

    def test_blocked_or_broken_checks_never_retire_anything(self):
        s = [self.sig("a")]
        state = {}
        for i, code in enumerate([403, None, 500, 429, 403]):
            state = {"links": publish.check_links(s, state, self.now + timedelta(hours=25 * (i + 1)), lambda u, c=code: c)[0]}
        self.assertEqual(publish.check_links(s, state, self.now + timedelta(days=9), lambda u: 403)[1], set())

    def test_links_are_checked_about_once_a_day_and_once_per_url(self):
        calls = []
        s = [self.sig("a"), self.sig("b")]
        publish.check_links(s, {}, self.now, lambda u: calls.append(u) or 200)
        self.assertEqual(len(calls), 1)
        state = {"links": publish.check_links(s, {}, self.now, lambda u: 200)[0]}
        calls.clear()
        publish.check_links(s, state, self.now + timedelta(hours=2), lambda u: calls.append(u) or 200)
        self.assertEqual(calls, [])

    def test_retired_signals_disappear_from_the_feed(self):
        f = publish.build_feed(self.base, [self.sig("a"), self.sig("b", url="https://example.test/b")], {}, self.empty, self.now, retired={"a"})
        self.assertEqual([x["id"] for x in f["signals"]], ["b"])

    def test_signals_past_the_maximum_age_fall_away_unless_kept(self):
        old, kept, recent = self.sig("old", days=400), self.sig("kept", days=400, keep=True), self.sig("recent", days=100)
        f = publish.build_feed(self.base, [old, kept, recent], {}, self.empty, self.now)
        self.assertEqual({x["id"] for x in f["signals"]}, {"kept", "recent"})

    def test_link_cache_survives_in_the_state_and_forgets_removed_signals(self):
        links, _ = publish.check_links([self.sig("a")], {}, self.now, lambda u: 200)
        st = publish.update_state({"first_seen": {}}, set(), self.now, None, links)
        self.assertIn("https://example.test/a", st["links"])
        self.assertEqual(publish.update_state(st, set(), self.now)["links"], links)
        cleaned, _ = publish.check_links([self.sig("z", url="https://example.test/z")], st, self.now, lambda u: 200)
        self.assertNotIn("https://example.test/a", cleaned)


class Evidence(unittest.TestCase):
    """Sensible automation: DIFFERENT official statements about hostile activity, counted over 30 days, lift an area to Elevated."""

    def setUp(self):
        self.now = publish.now_utc()
        self.base = fresh_baseline()
        for a in self.base["areas"].values():
            a["level"] = 2
        self.empty = {"terror": None, "items": [], "weather": {}, "used": [], "issues": []}

    def item(self, title, days=3, source="GOV.UK: Ministry of Defence", summary="", iid=None):
        return {"id": iid or "i" + str(abs(hash(title)) % 10**7), "title": title, "summary": summary, "source": source, "url": "https://www.gov.uk/x",
                "date": (self.now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), "cats": ["cyber"], "background": False}

    def area(self, f, a):
        return next(x for x in f["areas"] if x["id"] == a)

    # ---- classification ----
    def test_hostile_activity_against_the_uk_counts(self):
        self.assertIn("cyber", publish.evidence_areas(self.item("NCSC exposes Russian state-linked cyber campaign against UK organisations", source="NCSC news")))
        self.assertIn("comms", publish.evidence_areas(self.item("Royal Navy shadows Russian vessels targeting UK undersea cables")))

    def test_routine_or_foreign_items_do_not_count(self):
        for title in ("New NCSC guidance for small businesses",
                      "Defence Secretary statement on Russian attacks on Ukrainian energy infrastructure",
                      "UK and Russia sanctions update",
                      "Recruitment fair dates announced"):
            self.assertEqual(publish.evidence_areas(self.item(title)), set(), title)

    def test_needs_a_hostile_actor_and_a_threat_and_an_area(self):
        self.assertEqual(publish.evidence_areas(self.item("Russian delegation visits the UK for energy talks")), set())      # no threat
        self.assertEqual(publish.evidence_areas(self.item("Cyber attack on a UK council")), set())                            # no hostile actor named

    # ---- counting ----
    def test_near_duplicate_statements_count_once(self):
        a = self.item("Russian state-linked cyber campaign targets UK networks", iid="a")
        b = self.item("UK and allies expose Russian state-linked cyber campaign targeting UK networks", iid="b")
        ev, _ = publish.compute_evidence([a, b], {}, self.now)
        self.assertEqual(ev["cyber"]["count"], 1)

    def test_two_different_statements_count_twice(self):
        a = self.item("Russian state-linked cyber campaign targets UK networks", iid="a")
        b = self.item("Iranian hackers target UK water utilities in cyber attack warning", iid="b", source="NCSC news")
        ev, store = publish.compute_evidence([a, b], {}, self.now)
        self.assertEqual(ev["cyber"]["count"], 2)
        self.assertEqual(len(store), 2)

    def test_evidence_is_remembered_between_runs_and_expires_after_the_window(self):
        a = self.item("Russian state-linked cyber campaign targets UK networks", days=20, iid="a")
        _, store = publish.compute_evidence([a], {}, self.now)
        ev, _ = publish.compute_evidence([], {"evidence": store}, self.now + timedelta(days=5))     # feed has rolled over, memory has not
        self.assertEqual(ev["cyber"]["count"], 1)
        ev, kept = publish.compute_evidence([], {"evidence": store}, self.now + timedelta(days=15))  # now 35 days old
        self.assertEqual((ev["cyber"]["count"], kept), (0, []))

    # ---- levels ----
    def test_two_statements_move_an_area_from_aware_to_elevated_without_the_editor(self):
        ev = {"cyber": {"count": 2, "items": [{"title": "T1", "source": "NCSC news", "date": "2026-09-01T00:00:00Z", "url": "https://x.test/1"}, {"title": "T2", "source": "GOV.UK", "date": "2026-09-05T00:00:00Z", "url": "https://x.test/2"}]}}
        f = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev), self.now)
        cyber = self.area(f, "cyber")
        self.assertEqual((cyber["level"], cyber["basis"]), (3, "evidence"))
        self.assertIn("Raised to Elevated by official evidence", cyber["reason"])
        self.assertEqual(len(cyber["evidence"]), 2)
        self.assertEqual(f["overall"]["level"], 3)     # the overall level is the highest area

    def test_one_statement_is_noted_but_does_not_change_the_level(self):
        ev = {"cyber": {"count": 1, "items": [{"title": "T1", "source": "NCSC news", "date": "2026-09-01T00:00:00Z", "url": "https://x.test/1"}]}}
        f = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev), self.now)
        cyber = self.area(f, "cyber")
        self.assertEqual(cyber["level"], 2)
        self.assertIn("2 different ones", cyber["reason"])
        self.assertEqual(len(cyber["evidence"]), 1)

    def test_the_level_falls_back_by_itself_when_the_evidence_ages_out(self):
        a = self.item("Russian state-linked cyber campaign targets UK networks", days=10, iid="a")
        b = self.item("Iranian hackers target UK water utilities in cyber attack warning", days=12, iid="b", source="NCSC news")
        ev_now, store = publish.compute_evidence([a, b], {}, self.now)
        up = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev_now), self.now)
        self.assertEqual(self.area(up, "cyber")["level"], 3)
        later = self.now + timedelta(days=25)
        ev_later, _ = publish.compute_evidence([], {"evidence": store}, later)
        down = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev_later), later)
        self.assertEqual(self.area(down, "cyber")["level"], 2)

    def test_evidence_alone_never_makes_high_or_critical(self):
        ev = {a: {"count": 50, "items": []} for a in publish.EVIDENCE_AREAS}
        f = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev), self.now)
        self.assertTrue(all(a["level"] <= 3 for a in f["areas"]))

    def test_terrorism_ignores_evidence_and_follows_mi5(self):
        ev = {"security": {"count": 9, "items": []}}
        f = publish.build_feed(self.base, [], {}, dict(self.empty, terror="MODERATE", evidence=ev), self.now)
        self.assertEqual(self.area(f, "security")["level"], 1)

    def test_evidence_lifts_an_area_whose_editor_floor_has_decayed(self):
        old = json.loads(json.dumps(self.base))
        for a in old["areas"].values():
            a["level"], a["as_of"] = 3, (self.now - timedelta(days=200)).strftime("%Y-%m-%d")
        ev = {"comms": {"count": 2, "items": []}}
        f = publish.build_feed(old, [], {}, dict(self.empty, terror="MODERATE", evidence=ev), self.now)
        self.assertEqual(self.area(f, "comms")["level"], 3)      # kept up by evidence
        self.assertEqual(self.area(f, "cyber")["level"], 2)      # no evidence: settled to Aware

    def test_evidence_can_be_switched_off_by_the_brake_setting(self):
        ev = {"cyber": {"count": 5, "items": []}}
        off = dict(self.empty, terror="MODERATE", evidence={})
        self.assertEqual(self.area(publish.build_feed(self.base, [], {}, off, self.now), "cyber")["level"], 2)

    def test_end_to_end_two_official_statements_raise_an_area_through_the_real_publisher(self):
        import shutil
        import subprocess
        tmp = tempfile.mkdtemp()
        proj = os.path.join(tmp, "proj")
        shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns("site", "data", "__pycache__", ".git"))
        b = json.load(open(os.path.join(proj, "editorial", "baseline.json")))
        for a in b["areas"].values():
            a["level"], a["as_of"] = 2, self.now.strftime("%Y-%m-%d")
        json.dump(b, open(os.path.join(proj, "editorial", "baseline.json"), "w"))
        fx = os.path.join(tmp, "fx")
        os.makedirs(fx)
        put = lambda n, x: open(os.path.join(fx, n), "w").write(x)
        put("mi5-level.html", "<p>The current national threat level is MODERATE.</p>")
        for n in ("gov-homeoffice.atom", "gov-cabinet.atom", "gov-desnz.atom"):
            put(n, '<rss version="2.0"><channel></channel></rss>')
        put("ncsc.xml", rss([("NCSC warns of Iranian hackers targeting UK water utilities in cyber attack", "Advice.", self.now - timedelta(days=4))]))
        put("gov-mod.atom", rss([("Russian state-linked cyber campaign targets UK defence networks, Ministry of Defence says", "Statement.", self.now - timedelta(days=2))]))
        out, st = os.path.join(tmp, "site", "feed.json"), os.path.join(tmp, "state.json")
        subprocess.run([sys.executable, os.path.join(proj, "tools", "publish.py"), "--fixtures", fx, "--state", st, "--out", out], check=True, capture_output=True)
        f = json.load(open(out))
        cyber = next(a for a in f["areas"] if a["id"] == "cyber")
        self.assertEqual((cyber["level"], cyber["basis"]), (3, "evidence"))
        self.assertEqual(len(cyber["evidence"]), 2)
        self.assertEqual(f["overall"]["level"], 3)
        self.assertEqual(len(json.load(open(st))["evidence"]), 2)


class LevelPreview(unittest.TestCase):
    def test_five_scenarios_cover_levels_one_to_five(self):
        import build_site
        baseline = fresh_baseline()
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
