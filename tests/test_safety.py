"""SAFETY CHECKS. If any of these fail, the workflow publishes NOTHING: the last good version stays live and the page says "Out of date" after
about 3 hours. That is deliberate. A stale, labelled page is the site's honest way of saying "I don't know".

The governing principle: the site must never become MORE reassuring merely because it knows LESS.
It is also never allowed to raise an alarm on weak evidence.

This file is self-contained. It builds its own tiny project (with its own editorial data), so an owner's edits to baseline.json, signals.json
or site.json can never break it, and it needs nothing from the tests/data folder. It runs the real publisher, one run after another with
a controllable clock, so it tests what actually happens across outages, not just what single functions return.
Run it:  python -m unittest discover -s tests -p "test_safety.py" -v
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publish  # noqa: E402

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
NATIONAL = "The threat to the UK (England, Wales, Scotland and Northern Ireland) from all forms of terrorism is {}."
NI_LINE = "The threat to Northern Ireland from Northern Ireland-related terrorism is {}."
UKW = ("gov-mod", "gov-homeoffice", "gov-cabinet", "gov-desnz", "ncsc")


def baseline(as_of="2026-10-01", levels=None):
    lv = dict(energy=2, cyber=2, comms=2, security=3, military=2, supply=2)
    lv.update(levels or {})
    return {"scale": ["1", "2", "3", "4", "5"], "official_terror_map": {"LOW": 1, "MODERATE": 1, "SUBSTANTIAL": 2, "SEVERE": 3, "CRITICAL": 4},
            "official_terror_baseline": {"level": "SEVERE", "as_of": "2026-04-30", "source": "test"},
            "official_ni_baseline": {"level": "SUBSTANTIAL", "as_of": "2026-09-19", "source": "test"},
            "areas": {a: {"level": lv[a], "as_of": as_of, "status": f"{a} status", "reason": f"{a} reason"} for a in publish.AREAS}}


SOURCES = {"sources": [
    {"id": "mi5-level", "name": "MI5 threat level page (back-up)", "kind": "mi5_level", "url": "https://www.mi5.gov.uk/threats-and-advice/terrorism-threat-levels", "licence": "Crown"},
    {"id": "gov-terror", "name": "GOV.UK: Terrorism threat level", "kind": "mi5_level", "url": "https://www.gov.uk/terrorism-national-emergency", "licence": "OGL"},
    {"id": "gov-mod", "name": "GOV.UK: Ministry of Defence", "kind": "rss", "url": "https://www.gov.uk/government/organisations/ministry-of-defence.atom", "licence": "OGL"},
    {"id": "gov-homeoffice", "name": "GOV.UK: Home Office", "kind": "rss", "url": "https://www.gov.uk/government/organisations/home-office.atom", "licence": "OGL"},
    {"id": "gov-cabinet", "name": "GOV.UK: Cabinet Office", "kind": "rss", "url": "https://www.gov.uk/government/organisations/cabinet-office.atom", "licence": "OGL"},
    {"id": "gov-desnz", "name": "GOV.UK: Energy Security and Net Zero", "kind": "rss", "url": "https://www.gov.uk/government/organisations/desnz.atom", "licence": "OGL"},
    {"id": "ncsc", "name": "NCSC news", "kind": "rss", "url": "https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml", "licence": "OGL", "default_area": "cyber"}],
    "met_regions": [], "approved_hosts": ["gov.uk"]}


def item(title, cats=("cyber",), source="NCSC news", summary="", hours_ago=3):
    return {"id": "x", "cats": list(cats), "title": title, "summary": summary, "source": source, "url": "https://www.gov.uk/x/" + hashlib.md5(title.encode()).hexdigest()[:8],
            "date": publish.iso(T0 - hours_ago * H), "background": False}


class Project:
    """A throwaway copy of the code with its own editorial data, run one publisher pass at a time with a controllable clock."""

    def __init__(self, **bl):
        self.dir = tempfile.mkdtemp()
        self.p = os.path.join(self.dir, "p")
        shutil.copytree(os.path.join(ROOT, "tools"), os.path.join(self.p, "tools"), ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "src"), os.path.join(self.p, "src"))
        os.makedirs(os.path.join(self.p, "editorial"))
        self.write("editorial/baseline.json", baseline(**bl))
        self.write("editorial/signals.json", {"signals": []})
        self.write("editorial/history.json", {"entries": []})
        self.write("editorial/notice.json", {"text": ""})
        self.write("official_sources.json", SOURCES)
        self.write("site.json", {"name": "Wary", "short_name": "Wary", "tagline": "Watchful, not worried.", "description": "Test", "owner": "Test", "contact": "", "donate_url": "",
                                 "host": "GitHub Pages", "site_url": "", "auto_levels": True, "auto_headlines": True})
        self.fx = os.path.join(self.dir, "fx")
        self.state = os.path.join(self.p, "data", "state.json")
        self.out = os.path.join(self.p, "site", "feed.json")

    def write(self, rel, obj):
        path = os.path.join(self.p, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def set_site(self, **kw):
        c = json.loads(get(os.path.join(self.p, "site.json")))
        c.update(kw)
        self.write("site.json", c)

    def feeds(self, items=None, terror=None, ni=None, grid=None, prev_state=None, press=None):
        """Write the fixtures for the NEXT run. Anything not written is 'unavailable'."""
        shutil.rmtree(self.fx, ignore_errors=True)
        os.makedirs(self.fx)
        for sid in UKW:
            rows = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description><pubDate>{format_datetime(w)}</pubDate></item>"
                           for t, d, w, u in [(x[0], x[1], x[2], "https://www.gov.uk/x/" + hashlib.md5(x[0].encode()).hexdigest()[:8]) for x in (items or {}).get(sid, [])])
            put(os.path.join(self.fx, sid + ".xml"), f'<rss version="2.0"><channel>{rows}</channel></rss>')
        if items is None:
            for sid in UKW:
                os.remove(os.path.join(self.fx, sid + ".xml"))          # feeds unavailable, not merely empty
        if terror:
            page = "<p>" + NATIONAL.format(terror) + (" " + NI_LINE.format(ni) if ni else "") + "</p>"
            put(os.path.join(self.fx, "gov-terror.html"), page)
        if grid is not None:
            put(os.path.join(self.fx, "grid.json"), json.dumps(grid))
        if prev_state is not None:
            put(os.path.join(self.fx, "prev-state.json"), json.dumps(prev_state))
        if press is not None:
            for name in ("bbc-uk.xml", "bbc-world.xml"):
                rows = "".join(f"<item><title>{t}</title><link>https://www.bbc.co.uk/news/{i}</link><description>d</description><pubDate>{format_datetime(w)}</pubDate></item>"
                               for i, (t, w) in enumerate(press if name == "bbc-uk.xml" else []))
                put(os.path.join(self.fx, name), f'<rss version="2.0"><channel>{rows}</channel></rss>')

    def run(self, now, expect_ok=True):
        env = dict(os.environ, WARY_NOW=publish.iso(now), GITHUB_SHA="abcdef123456", GITHUB_RUN_NUMBER="7")
        r = subprocess.run([sys.executable, os.path.join(self.p, "tools", "publish.py"), "--fixtures", self.fx, "--state", self.state, "--out", self.out],
                           capture_output=True, text=True, env=env, cwd=self.p)
        if expect_ok and r.returncode != 0:
            raise AssertionError("the publisher failed:\n" + r.stderr[-800:])
        return json.loads(get(self.out)) if r.returncode == 0 else None

    def saved_state(self):
        return json.loads(get(self.state))


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def get(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def area(feed, a):
    return next(x for x in feed["areas"] if x["id"] == a)


def tile(feed, i):
    return next(c for c in feed["checks"] if c["id"] == i)


# ======================================================================================================================================
class SafetyMi5Reading(unittest.TestCase):
    def test_the_real_mi5_sentence(self):
        self.assertEqual(publish.read_terror_level(("<p>" + NATIONAL.format("SEVERE") + "*</p>").encode()), ("SEVERE", True))

    def test_the_govuk_sentence(self):
        page = "<p>The threat to the UK (England, Wales, Scotland and Northern Ireland) from terrorism is substantial.</p>"
        self.assertEqual(publish.read_terror_level(page.encode()), ("SUBSTANTIAL", True))

    def test_the_northern_ireland_line_is_never_mistaken_for_the_national_level(self):
        page = "<p>" + NI_LINE.format("SEVERE") + " " + NATIONAL.format("MODERATE") + "</p>"
        self.assertEqual(publish.read_terror_level(page.encode())[0], "MODERATE")

    def test_the_northern_ireland_level_is_read_separately(self):
        self.assertEqual(publish.read_ni_level(("<p>" + NATIONAL.format("SEVERE") + " " + NI_LINE.format("SUBSTANTIAL") + "</p>").encode()), "SUBSTANTIAL")
        self.assertIsNone(publish.read_ni_level(("<p>" + NATIONAL.format("SEVERE") + "</p>").encode()))

    def test_a_definitions_list_is_not_read_as_the_level(self):
        page = b"<ul><li>CRITICAL means an attack is highly likely in the near future</li><li>SEVERE means highly likely</li></ul>"
        found, contextual = publish.read_terror_level(page)
        self.assertFalse(contextual, "a bare word from a definitions list must never count as a confident reading")

    def test_a_page_with_no_level_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            publish.read_terror_level(b"<p>Site maintenance. Back soon.</p>")

    def test_a_big_jump_is_held_before_it_is_believed(self):
        now, state = T0, {"first_seen": {}}
        ok, key = publish.accept_terror("LOW", True, "SEVERE", state, now, 6)
        self.assertFalse(ok)                                                       # SEVERE -> LOW in one reading: not believed yet
        state["first_seen"][key] = publish.iso(now - 7 * H)
        self.assertTrue(publish.accept_terror("LOW", True, "SEVERE", state, now, 6)[0])   # believed once it has held


class SafetyLastKnownTerrorism(unittest.TestCase):
    """The core rule: if the terrorism source becomes unreadable the site keeps the LAST CONFIRMED level. It never falls back to an older
    editorial figure or drops a level because it lost sight of the source."""

    def test_critical_survives_an_outage_and_level_5_needs_recent_confirmation(self):
        p = Project()
        p.feeds(items={}, terror="CRITICAL")
        self.assertEqual(area(p.run(T0), "security")["level"], 4)                  # just seen: High, not yet held
        p.feeds(items={}, terror="CRITICAL")
        f = p.run(T0 + 7 * H)
        self.assertEqual((area(f, "security")["level"], f["overall"]["level"]), (5, 5))    # held 6h+ and confirmed live: Critical
        p.feeds(items={})                                                          # source now unreadable
        f = p.run(T0 + 8 * H)
        self.assertEqual(area(f, "security")["level"], 5)                          # last confirmed only 1h ago: still fresh
        self.assertIn("not confirmed live", tile(f, "terror")["text"])
        f = p.run(T0 + 12 * H)
        self.assertEqual(area(f, "security")["level"], 4)                          # not confirmed for 5h: capped at High, NEVER lowered below it
        self.assertIn("not confirmed live", tile(f, "terror")["text"])
        self.assertIn("mi5-critical", p.saved_state()["first_seen"], "the hold timer must survive an outage")
        self.assertFalse(f["official"]["live_checked"])

    def test_a_real_lower_reading_still_lowers_the_level(self):
        p = Project()
        p.feeds(items={}, terror="CRITICAL")
        p.run(T0)
        p.feeds(items={}, terror="SEVERE")
        f = p.run(T0 + H)                                                          # a LIVE, lower reading is real evidence
        self.assertEqual(area(f, "security")["level"], 3)
        self.assertNotIn("mi5-critical", p.saved_state()["first_seen"])

    def test_a_faded_editor_baseline_cannot_lower_the_level_during_an_outage(self):
        p = Project(as_of="2026-01-01")                                            # the editor's standing text is 9 months old
        p.feeds(items={}, terror="SEVERE")
        self.assertEqual(area(p.run(T0), "security")["level"], 3)
        p.feeds(items={})
        f = p.run(T0 + H)
        self.assertEqual(area(f, "security")["level"], 3, "losing sight of the source must not make the site more reassuring")

    def test_a_cold_start_with_no_source_uses_the_editors_confirmed_level_and_says_so(self):
        p = Project()
        p.feeds(items={})
        f = p.run(T0)
        self.assertEqual(area(f, "security")["level"], 3)
        self.assertIn("not confirmed live", tile(f, "terror")["text"])
        self.assertEqual(f["build"]["memory"], "cold start")
        self.assertTrue(any("Memory was reset" in i for i in f["data_issues"]))

    def test_the_last_known_level_is_labelled_with_when_it_was_confirmed(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE")
        p.run(T0)
        p.feeds(items={})
        f = p.run(T0 + 5 * H)
        self.assertIn("Last confirmed", tile(f, "terror")["detail"])
        self.assertIn("last confirmed", area(f, "security")["status"])

    def test_the_northern_ireland_level_is_live_then_last_known(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE", ni="SUBSTANTIAL")
        f = p.run(T0)
        self.assertEqual((f["official"]["ni_level"], f["official"]["ni_live"]), ("SUBSTANTIAL", True))
        p.feeds(items={})
        f = p.run(T0 + 2 * H)
        self.assertEqual((f["official"]["ni_level"], f["official"]["ni_live"]), ("SUBSTANTIAL", False), "a remembered NI level must never be presented as live")

    def test_a_terrorism_source_that_is_down_is_reported_plainly(self):
        p = Project()
        p.feeds(items={})
        f = p.run(T0)
        self.assertTrue(any("Terrorism threat level" in i and "(" in i for i in f["data_issues"]), f["data_issues"])


class SafetyTriggers(unittest.TestCase):
    """Automatic HIGH triggers: strict, official-only, and durable for their whole defined life."""

    def test_cyber_high_needs_a_current_incident_with_public_impact(self):
        high = lambda t, **k: publish.cyber_high(item(t, **k))
        self.assertTrue(high("NCSC statement on a nationally significant cyber incident affecting UK hospitals"))
        self.assertFalse(high("NCSC Annual Review: 204 nationally significant incidents handled in the year to August"))
        self.assertFalse(high("NCSC publishes statistics on nationally significant incidents"))
        self.assertFalse(high("Guidance for organisations after a nationally significant incident at a UK university"))
        self.assertFalse(high("NCSC responding to a nationally significant incident at a private company"))     # no essential-service or public impact named
        self.assertFalse(high("NCSC statement on a significant incident affecting UK hospitals"))                # not the formal category
        self.assertFalse(high("NCSC statement on a nationally significant cyber incident affecting UK hospitals", cats=("energy",)))

    def test_cable_high_needs_a_real_uk_connection(self):
        high = lambda t: publish.cable_high(item(t, cats=("comms",), source="GOV.UK: Ministry of Defence"))
        self.assertTrue(high("Ministry of Defence statement on damage to a subsea cable serving the UK"))
        self.assertTrue(high("Damage to an undersea cable in UK waters confirmed"))
        self.assertFalse(high("UK statement on damage to a subsea cable in the Baltic Sea"))                     # the word 'UK' alone is not a UK link
        self.assertFalse(high("Finland detains a ship after damage to an undersea cable between Finland and Estonia"))
        self.assertFalse(high("Foreign Secretary condemns sabotage of undersea cables in the Baltic"))
        self.assertFalse(high("Undersea cable protection strategy published"))
        self.assertFalse(high("Russian submarine activity near UK undersea cables"))                             # activity, not damage

    def test_a_trigger_survives_a_failed_fetch_and_expires_on_time(self):
        p = Project()
        title = "Ministry of Defence statement on damage to a subsea cable serving the UK"
        p.feeds(items={"gov-mod": [(title, "d", T0 - 2 * H)]}, terror="SEVERE")
        self.assertEqual(area(p.run(T0), "comms")["level"], 4)
        p.feeds(items=None, terror="SEVERE")                                       # every feed unavailable
        self.assertEqual(area(p.run(T0 + H), "comms")["level"], 4, "one bad fetch must not clear an accepted HIGH")
        self.assertEqual(area(p.run(T0 + 68 * H), "comms")["level"], 4)
        f = p.run(T0 + 71 * H)                                                     # 73h after the statement: its 72h life is over
        self.assertEqual(area(f, "comms")["level"], 2)

    def test_being_seen_again_does_not_extend_a_trigger(self):
        p = Project()
        title = "Ministry of Defence statement on damage to a subsea cable serving the UK"
        for hours in (0, 30, 60, 71):
            p.feeds(items={"gov-mod": [(title, "d", T0 - 2 * H)]}, terror="SEVERE")
            f = p.run(T0 + hours * H)
        self.assertEqual(area(f, "comms")["level"], 2, "the same statement, seen for 73 hours, must not stay HIGH")

    def test_a_grid_emergency_warning_also_survives_a_failed_fetch(self):
        p = Project()
        warn = {"data": [{"publishTime": publish.iso(T0 - H), "warningText": "DEMAND CONTROL IMMINENT"}]}
        p.feeds(items={}, terror="SEVERE", grid=warn)
        self.assertEqual(area(p.run(T0), "energy")["level"], 4)
        p.feeds(items={}, terror="SEVERE")                                         # grid source unreadable now
        self.assertEqual(area(p.run(T0 + 2 * H), "energy")["level"], 4)
        self.assertEqual(area(p.run(T0 + 73 * H), "energy")["level"], 2)

    def test_attack_on_the_uk_plus_cobr_works_on_real_feed_data_and_level_5_must_hold(self):
        """Regression: these headlines match no area keyword, and used to be dropped before the rule saw them."""
        p = Project()
        items = {"gov-homeoffice": [("Home Secretary statement on the attack on the UK", "d", T0 - 1 * H)],
                 "gov-cabinet": [("Prime Minister chairs COBR meeting", "d", T0 - 1 * H)]}
        p.feeds(items=items, terror="SEVERE")
        f = p.run(T0)
        self.assertEqual(f["overall"]["level"], 4)                                  # High, not Critical, until it has held
        self.assertTrue(f["auto_notices"], "the COBR notice should appear")
        p.feeds(items=items, terror="SEVERE")
        self.assertEqual(p.run(T0 + 7 * H)["overall"]["level"], 5)                 # held for 6h+ and seen again recently
        p.feeds(items=None, terror="SEVERE")                                       # then the feeds go dark
        self.assertEqual(p.run(T0 + 12 * H)["overall"]["level"], 4, "without recent confirmation the level is capped at High, and not dropped")

    def test_nothing_from_the_press_or_evidence_alone_can_reach_level_5(self):
        p = Project()
        many = [(f"Russian state-linked hackers attack UK energy network number {i}", "d", T0 - i * H) for i in range(1, 6)]
        p.feeds(items={"ncsc": many, "gov-cabinet": many}, terror="SEVERE")
        f = p.run(T0)
        self.assertLessEqual(f["overall"]["level"], 3)

    def test_the_emergency_brake_stops_all_automatic_raising(self):
        p = Project()
        p.set_site(auto_levels=False)
        title = "Ministry of Defence statement on damage to a subsea cable serving the UK"
        p.feeds(items={"gov-mod": [(title, "d", T0 - H)]}, terror="SEVERE")
        f = p.run(T0)
        self.assertEqual(area(f, "comms")["level"], 2)
        self.assertEqual(f["auto_notices"], [])


class SafetyEvidence(unittest.TestCase):
    def two_statements(self, hours_ago=48):
        return {"ncsc": [("NCSC warns of Iranian hackers targeting UK water utilities in cyber attack", "d", T0 - hours_ago * H)],
                "gov-cabinet": [("Russian state-linked cyber campaign targets UK defence networks, Cabinet Office says", "d", T0 - hours_ago * H)]}

    def test_two_different_official_statements_raise_an_area_and_a_remembered_pair_survives_outages(self):
        p = Project()                                                               # every area starts at Aware, so evidence is the only way up
        p.feeds(items=self.two_statements(), terror="MODERATE")
        f = p.run(T0)
        self.assertEqual((area(f, "cyber")["level"], area(f, "cyber")["basis"]), (3, "evidence"))
        p.feeds(items=None, terror="MODERATE")                                      # every feed now unavailable
        f = p.run(T0 + H)
        self.assertEqual(area(f, "cyber")["level"], 3, "remembered evidence must survive an outage")
        self.assertEqual(len(area(f, "cyber")["evidence"]), 2)

    def test_evidence_ages_out_by_itself(self):
        p = Project()
        p.feeds(items=self.two_statements(), terror="MODERATE")
        p.run(T0)
        p.feeds(items={}, terror="MODERATE")
        f = p.run(T0 + 31 * 24 * H)
        self.assertEqual(area(f, "cyber")["level"], 2)

    def test_one_statement_or_a_repeat_of_it_is_not_enough(self):
        p = Project()
        one = {"ncsc": [("NCSC warns of Iranian hackers targeting UK water utilities in cyber attack", "d", T0 - 48 * H)]}
        p.feeds(items=one, terror="MODERATE")
        self.assertEqual(area(p.run(T0), "cyber")["level"], 2)

    def test_terrorism_ignores_evidence_and_follows_the_official_level(self):
        p = Project()
        p.feeds(items=self.two_statements(), terror="SUBSTANTIAL")                # one step below SEVERE, so it is believed at once
        self.assertEqual(area(p.run(T0), "security")["level"], 2)

    def test_a_large_drop_is_held_for_confirmation_before_the_level_falls(self):
        p = Project()
        p.feeds(items={}, terror="MODERATE")                                       # SEVERE -> MODERATE skips a step: not believed on one reading
        f = p.run(T0)
        self.assertEqual(area(f, "security")["level"], 3)
        self.assertTrue(any("large jump" in i or "last accepted" in i for i in f["data_issues"]), f["data_issues"])


class SafetyState(unittest.TestCase):
    """The site carries its own memory, and no damaged memory can stop it publishing or lower a level."""

    def test_a_corrupt_cache_file_never_crashes_the_publisher(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE")
        p.run(T0)
        put(p.state, '{"first_seen": {"mi5-critical": "2026-10')          # truncated mid-write
        f = p.run(T0 + H)
        self.assertEqual(f["build"]["memory"], "cold start")

    def test_a_tampered_state_is_treated_as_missing(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE")
        p.run(T0)
        st = p.saved_state()
        st["first_seen"]["mi5-critical"] = "2020-01-01T00:00:00Z"                  # forged: would make Level 5 instantly 'held'
        put(p.state, json.dumps(st))
        self.assertEqual(p.run(T0 + H)["build"]["memory"], "cold start")

    def test_the_published_state_file_is_valid_and_sits_beside_the_feed(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE")
        p.run(T0)
        published = json.loads(get(os.path.join(os.path.dirname(p.out), "state.json")))
        self.assertTrue(publish.valid_state(published))

    def test_the_previous_published_state_can_stand_in_for_a_lost_cache(self):
        p = Project()
        p.feeds(items={}, terror="CRITICAL")
        p.run(T0)
        prev = json.loads(get(os.path.join(os.path.dirname(p.out), "state.json")))
        os.remove(p.state)                                                         # the cache is gone (evicted, or a run was cancelled)
        p.feeds(items={}, terror="CRITICAL", prev_state=prev)
        f = p.run(T0 + 7 * H)
        self.assertEqual(f["build"]["memory"], "previous site state")
        self.assertEqual(area(f, "security")["level"], 5, "the hold timer must come through the site's own memory")

    def test_the_newest_valid_copy_wins(self):
        p = Project()
        old = publish.seal_state({"first_seen": {}}, T0 - 5 * H)
        new = publish.seal_state({"first_seen": {}, "terror_last": "CRITICAL"}, T0)
        for cache, site, expect in ((new, old, "cache"), (old, new, "previous site state")):
            os.makedirs(os.path.dirname(p.state), exist_ok=True)
            put(p.state, json.dumps(cache))
            p.feeds(items={}, prev_state=site)
            self.assertEqual(publish.load_memory(p.state, "", p.fx, T0)[1], expect)

    def test_an_older_format_cache_is_still_used(self):
        p = Project()
        os.makedirs(os.path.dirname(p.state), exist_ok=True)
        put(p.state, json.dumps({"first_seen": {}, "terror_last": "CRITICAL"}))
        p.feeds(items={})
        f = p.run(T0)
        self.assertIn("older format", f["build"]["memory"])
        self.assertEqual(area(f, "security")["level"], 4)

    def test_no_half_written_files_are_left_behind(self):
        p = Project()
        p.feeds(items={}, terror="SEVERE")
        p.run(T0)
        leftovers = [f for d, _, fs in os.walk(p.dir) for f in fs if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class SafetyUnknownIsNeverClear(unittest.TestCase):
    """A live check that cannot be read must say 'unknown', never 'clear'."""

    def fx(self, **files):
        d = tempfile.mkdtemp()
        for name, content in files.items():
            put(os.path.join(d, name), content)
        return d

    def test_every_live_check_degrades_to_unknown(self):
        for checker, empty, garbage in ((publish.check_alerts, {}, {"alerts.html": "<html>Service unavailable</html>"}),
                                        (publish.check_grid, {}, {"grid.json": "<html>oops</html>"}),
                                        (publish.check_space, {}, {"space.json": "not json"})):
            self.assertEqual(checker(self.fx(**empty), T0)["state"], "unknown", checker.__name__)
            self.assertEqual(checker(self.fx(**garbage), T0)["state"], "unknown", checker.__name__)

    def test_alerts_say_none_current_only_when_the_page_positively_says_so(self):
        self.assertEqual(publish.check_alerts(self.fx(**{"alerts.html": "<p>There are no current alerts.</p>"}), T0)["state"], "clear")
        self.assertEqual(publish.check_alerts(self.fx(**{"alerts.html": "<p>Welcome to GOV.UK</p>"}), T0)["state"], "unknown")

    def test_one_source_failing_never_stops_publishing_and_is_reported_with_a_reason(self):
        p = Project()
        p.feeds(items={"gov-mod": []}, terror="SEVERE")
        os.remove(os.path.join(p.fx, "gov-desnz.xml"))
        f = p.run(T0)
        self.assertTrue(any(i.startswith("GOV.UK: Energy Security and Net Zero (") for i in f["data_issues"]), f["data_issues"])

    def test_a_source_on_an_unapproved_host_is_never_fetched(self):
        doc = json.loads(json.dumps(SOURCES))
        doc["sources"].append({"id": "evil", "name": "Not official", "kind": "rss", "url": "https://evil.example.com/feed.xml", "licence": "?"})
        fx = tempfile.mkdtemp()
        out = publish.gather_official(doc, fx, T0, "SEVERE", {}, 6, "")
        self.assertTrue(any("not on the approved official list" in i for i in out["issues"]))
        self.assertNotIn("Not official", [u["name"] for u in out["used"]])


class SafetyHandsOff(unittest.TestCase):
    """The site is meant to run itself. By default no hand-written launch wording or standing level is shown, whatever old numbers are in site.json."""

    def test_no_hand_written_wording_is_shown_by_default_even_with_an_old_decay_setting(self):
        p = Project(levels={"comms": 3, "military": 3})
        p.set_site(decay_days=90)                                                  # what an older site.json still says
        p.feeds(items={}, terror="SEVERE")
        f = p.run(T0)
        for a in ("military", "comms", "cyber"):
            self.assertNotIn("reason", area(f, a)["reason"] + " " + area(f, a)["status"], f"{a} is still showing hand-written text")
        self.assertEqual(area(f, "comms")["level"], 2, "a hand-set standing level must not hold the level up when running automatically")
        self.assertTrue(f["hands_off"])

    def test_hand_written_wording_can_be_switched_back_on(self):
        p = Project()
        p.set_site(hands_off=False)
        p.feeds(items={}, terror="SEVERE")
        f = p.run(T0)
        self.assertIn("military reason", area(f, "military")["reason"])
        self.assertFalse(f["hands_off"])

    def test_an_unrelated_ministry_of_defence_publication_is_not_presented_as_a_threat_signal(self):
        p = Project()
        item_ = ("Guidance: Land Open Systems Architecture (LOSA)", "Land Open Systems Architecture (LOSA) is the Ministry of Defence's approach to support land capability integration.", T0 - 3 * H)
        p.feeds(items={"gov-mod": [item_]}, terror="SEVERE")
        f = p.run(T0)
        self.assertNotIn("Land Open Systems", area(f, "military")["reason"])
        self.assertNotIn("latest official item", area(f, "military")["reason"])
        self.assertFalse([s for s in f["signals"] if "Land Open Systems" in s["title"] and "military" in s["cats"]])

    def test_a_genuinely_relevant_defence_item_is_used(self):
        p = Project()
        item_ = ("Defence Secretary statement on Russian submarine activity near UK waters", "The Ministry of Defence says a Russian submarine was tracked and has left.", T0 - 3 * H)
        p.feeds(items={"gov-mod": [item_]}, terror="SEVERE")
        f = p.run(T0)
        self.assertIn("latest official item", area(f, "military")["reason"])
        self.assertIn("Russian submarine activity near UK waters", area(f, "military")["reason"])


class SafetyCounter(unittest.TestCase):
    """The optional visit counter must be truly anonymous, off by default, and never able to break the page."""

    def build(self, counter_url=None, tmp=None):
        import shutil, subprocess
        tmp = tmp or tempfile.mkdtemp()
        p = os.path.join(tmp, "p")
        shutil.copytree(os.path.join(ROOT, "tools"), os.path.join(p, "tools"), ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "src"), os.path.join(p, "src"))
        os.makedirs(os.path.join(p, "editorial"))
        for rel, obj in (("editorial/baseline.json", baseline()), ("editorial/signals.json", {"signals": []}), ("editorial/history.json", {"entries": []}),
                        ("editorial/notice.json", {"text": ""}), ("official_sources.json", SOURCES)):
            put(os.path.join(p, rel), json.dumps(obj))
        site = {"name": "Wary", "short_name": "Wary", "tagline": "Watchful, not worried.", "description": "Test", "owner": "Test", "contact": "",
                "donate_url": "", "host": "GitHub Pages", "site_url": "", "auto_levels": True, "auto_headlines": True}
        if counter_url is not None:
            site["counter_url"] = counter_url
        put(os.path.join(p, "site.json"), json.dumps(site))
        r = subprocess.run([sys.executable, os.path.join(p, "tools", "build_site.py")], capture_output=True, text=True, cwd=p, env=dict(os.environ, WARY_NOW=publish.iso(T0)))
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        return get(os.path.join(p, "site", "index.html")), r.stderr

    def test_off_by_default_no_worker_address_anywhere_and_no_placeholder_left_behind(self):
        page, _ = self.build()  # counter_url not set at all, same as an owner's existing site.json
        self.assertNotIn("workers.dev", page)
        self.assertNotIn("{{COUNTER", page)
        self.assertIn("var CU='';", page, "with no counter configured the guard variable must be empty, so the if(CU) check never fires")

    def test_switched_on_adds_the_address_to_the_page_and_the_security_policy_and_nowhere_else(self):
        page, _ = self.build("https://wary-counter.example.workers.dev/count")
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
        self.assertIn("https://wary-counter.example.workers.dev", csp)
        self.assertEqual(csp.count("wary-counter.example.workers.dev"), 1, "the address must appear in connect-src only, nowhere else in the policy")
        self.assertIn("https://wary-counter.example.workers.dev/count", page)

    def test_a_malformed_address_is_rejected_and_the_counter_stays_off(self):
        page, err = self.build("not a url; <script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("workers.dev", page)
        self.assertIn("does not look like a plain https address", err)

    def test_only_the_origin_reaches_the_security_policy_never_a_path_or_query(self):
        page, _ = self.build("https://wary-counter.example.workers.dev/count?key=shouldnotleak")
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
        self.assertNotIn("key=", csp)
        self.assertNotIn("shouldnotleak", csp)

    def test_the_ping_is_fire_and_forget_it_is_never_awaited_and_always_wrapped_in_try_catch(self):
        page, _ = self.build("https://wary-counter.example.workers.dev/count")
        block = page[page.index("var CU="):page.index("var CU=") + 260]
        self.assertIn("try{", page[max(0, page.index("var CU=") - 10):page.index("var CU=")])
        self.assertIn(".catch(function(){})", block, "a failed or blocked ping must be swallowed silently, never surfaced to the visitor")
        self.assertNotIn("await fetch", block)

    def test_the_counter_never_sends_a_cookie_and_the_page_never_reads_a_reply(self):
        page, _ = self.build("https://wary-counter.example.workers.dev/count")
        block = page[page.index("var CU="):page.index("var CU=") + 260]
        self.assertNotIn("credentials", block)                          # no credentials: 'include' — no cookie is ever sent
        self.assertIn("mode:'no-cors'", block)                          # the page cannot read anything back even if it tried

    def test_the_ping_fires_from_the_one_time_page_load_function_not_from_render(self):
        """render() runs on every setup change, retry, wipe and the 15-minute feed refresh. The ping must live
        outside it, in load(), which only ever runs once per page life, with its own explicit one-shot guard."""
        page, _ = self.build("https://wary-counter.example.workers.dev/count")
        render_fn = page[page.index("function render(){"):page.index("function render(){") + 400]
        self.assertNotIn("fetch(CU", render_fn, "the ping must not be inside render(), which fires many times per session")
        self.assertIn("var counted=false;", page)
        self.assertIn("if(counted) return; counted=true;", page)
        load_fn = page[page.index("function load(){"):page.index("function load(){") + 400]
        self.assertIn("pingCounterOnce()", load_fn)

    def test_a_url_containing_a_literal_quote_cannot_break_out_of_the_script(self):
        """counter_url is owner-controlled, not public input, but it must still be JSON-escaped, not trusted to
        never contain a stray quote or backslash, the same standard the rest of the page holds itself to."""
        mischief = "https://wary-counter.example.workers.dev/count?x=';document.write('bad"
        page, _ = self.build(mischief)
        line = page[page.index("var CU="):page.index("var CU=") + 200]
        self.assertNotIn("x=';document.write(", line, "an unescaped quote here would end the string and let this run as code")
        self.assertIn("x=\\';document.write(", line, "the quote must be escaped, so the payload stays inert text inside the string")

    def test_the_about_page_is_honest_about_the_counter_only_when_it_exists(self):
        off, _ = self.build()
        on, _ = self.build("https://wary-counter.example.workers.dev/count")
        self.assertNotIn("This page counts visits", off)
        self.assertIn("This page counts visits", on)
        self.assertIn("no cookie, no ID", on)
        self.assertIn("sends no identifier or visitor information", on)
        self.assertNotIn("never receives anything else", on)   # too absolute a claim about a third party\'s own network



class SafetyPress(unittest.TestCase):
    def test_press_headlines_can_never_move_any_level_or_notice(self):
        """Even the most alarming BBC headlines, with the strip switched ON, are only ever shown. They never change a rating."""
        alarm = [("Terror attack on UK: COBR convened after attack on the UK as nationally significant cyber incident hits hospitals, blackout across Britain", T0 - 1 * H),
                 ("Undersea cable damage cuts UK internet; national emergency declared; missile strike on UK", T0 - 2 * H)]
        p = Project()
        p.set_site(bbc_headlines=True)
        p.feeds(items={}, terror="SEVERE", press=[])
        quiet = p.run(T0)
        p.feeds(items={}, terror="SEVERE", press=alarm)
        loud = p.run(T0 + timedelta(minutes=30))
        self.assertGreater(len(loud["press"]), 0, "the strip should show the headlines")
        self.assertEqual([a["level"] for a in quiet["areas"]], [a["level"] for a in loud["areas"]])
        self.assertEqual((quiet["overall"]["level"], loud["auto_notices"]), (loud["overall"]["level"], []))

    def test_a_broken_press_feed_is_harmless(self):
        p = Project()
        p.set_site(bbc_headlines=True)
        p.feeds(items={}, terror="SEVERE")
        f = p.run(T0)
        self.assertEqual(f["press"], [])


class SafetyPage(unittest.TestCase):
    """The page's own promises: no third parties, no unsafe HTML, and a readable copy without JavaScript."""

    @classmethod
    def setUpClass(cls):
        cls.src = get(os.path.join(ROOT, "src", "index.src.html"))

    def test_no_data_is_ever_written_into_the_page_as_markup(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(bad, self.src, f"the page must not use {bad}: feed text is only ever inserted as plain text")

    def test_the_security_policy_allows_only_the_two_public_services_the_page_needs(self):
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', self.src).group(1)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self' https://api.postcodes.io https://environment.data.gov.uk", csp)
        for directive in ("script-src", "style-src", "img-src"):
            self.assertNotRegex(re.search(directive + r"[^;]*", csp).group(0), r"https?://", directive)
        self.assertIn("form-action 'none'", csp)

    def test_the_page_loads_no_third_party_scripts_styles_fonts_or_images(self):
        for tag in re.findall(r"<(?:script|link|img|iframe|source)\b[^>]*>", self.src):
            self.assertNotRegex(tag, r'(?:src|href)="https?://', tag)

    def test_the_static_status_block_escapes_everything(self):
        feed = {"generated_at": publish.iso(T0), "overall": {"level": 3, "name": "Elevated"},
                "areas": [{"id": "energy", "name": "<img src=x onerror=alert(1)>", "level": 3, "status": '"><script>alert(1)</script>'}]}
        html = publish.prerender_html(feed)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_static_block_can_be_refreshed_again_and_again(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "index.html")
        put(path, "<body><noscript><!--PRERENDER-->old<!--/PRERENDER--></noscript></body>")
        feed = {"generated_at": publish.iso(T0), "overall": {"level": 3, "name": "Elevated"}, "areas": [{"id": "e", "name": "Energy", "level": 2, "status": "Calm."}]}
        for _ in range(3):
            self.assertTrue(publish.prerender_into(path, feed))
        html = get(path)
        self.assertEqual(html.count("<!--PRERENDER-->"), 1)
        self.assertIn("Household level: Elevated (3 of 5)", html)

    def test_the_page_template_has_the_markers_the_publisher_fills(self):
        self.assertIn("<!--PRERENDER-->", self.src)
        self.assertIn("<!--/PRERENDER-->", self.src)


class SafetyWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wf = os.path.join(ROOT, ".github", "workflows")
        cls.main = get(os.path.join(wf, "publish.yml"))
        cls.keep = get(os.path.join(wf, "keepalive.yml")) if os.path.exists(os.path.join(wf, "keepalive.yml")) else None

    def test_the_build_can_never_write_to_the_repository(self):
        self.assertNotIn("contents: write", self.main)
        self.assertIn("contents: read", self.main)

    def test_the_safety_step_blocks_publishing_and_runs_before_the_build(self):
        step = self.main[self.main.index("Safety checks"):self.main.index("Explain a safety failure")]
        self.assertNotIn("continue-on-error", step)
        self.assertLess(self.main.index("Safety checks"), self.main.index("Build the site"))
        self.assertIn('-p "test_safety.py"', step)

    def test_a_deployment_is_never_cancelled_half_way(self):
        block = self.main[self.main.index("concurrency:"):self.main.index("jobs:")]
        self.assertIn("cancel-in-progress: false", block)

    def test_scheduled_runs_avoid_the_busy_minutes(self):
        self.assertIn('"7,37 * * * *"', self.main)

    def test_browser_checks_run_on_code_changes_only_and_block_the_deploy(self):
        step = self.main[self.main.index("Browser checks"):self.main.index("configure-pages")]
        self.assertIn("github.event_name == 'push'", step)
        self.assertNotIn("continue-on-error", step)
        self.assertIn("tests/browser_check.py", step)

    def test_the_keep_alive_is_its_own_small_workflow(self):
        if self.keep is None:      # housekeeping, not safety: a missing file must never stop the site updating (the file check step warns about it)
            self.skipTest("keepalive.yml is not in .github/workflows. GitHub may switch the schedule off after 60 quiet days, so please add it.")
        self.assertIn("contents: write", self.keep)
        self.assertIn("--allow-empty", self.keep)
        self.assertIn("40", self.keep)
        for forbidden in ("tools/", "python", "deploy-pages", "upload-pages-artifact"):
            self.assertNotIn(forbidden, self.keep, "the one job with write permission must do nothing else")


if __name__ == "__main__":
    unittest.main()
