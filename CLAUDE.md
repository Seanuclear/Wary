# CLAUDE.md: working on Wary

This file briefs Claude Code (or anyone) working in this repository. Claude Code reads it automatically at the start of every session. Read all of it before changing anything.

**This repository is public.** Never add secrets, private notes, personal details, API keys or the visit counter's private address to this file or anywhere else in the repo.

Last updated: 28 September 2026.

---

## 1. What Wary is

Wary (https://wary.org.uk) is a free, independent, non-commercial website that gives UK households one calm threat level from 1 to 5, the six areas behind it, live checks from official sources, and a preparedness checklist that adapts to each visitor's household. Tagline: **"Watchful, not worried."**

- Scale: 1 Routine, 2 Aware, 3 Elevated, 4 High, 5 Critical. The scale is Wary's own, not an official warning system, and the page says so.
- Six areas: `energy` (Power, gas and fuel), `cyber` (Cyber attacks on services), `comms` (Cables, GPS and phone networks), `security` (Terrorism and sabotage), `military` (Military and nuclear escalation), `supply` (Food, water and supply chains).
- It is a slower, calmer supplement to Emergency Alerts, the emergency services, councils and the BBC. It never replaces them. Real emergencies reach people through Emergency Alerts first.
- It went live on 20 September 2026. It is run by one person, the owner (Sean).

## 2. Working with the owner

- Sean is not a developer (he was a webmaster about 20 years ago). He cannot run commands on his own machine and often works from his phone. Until now, every change reached the repo as a hand-built "update pack" (zip files with a START-HERE.txt) that he pasted into GitHub's web editor. With Claude Code attached to the repo, you can commit directly instead. That is the point of this handover.
- Write to him in plain, direct British English. Lead with the answer or the result. Say what changed, what he will see, and how to check it. Keep it short.
- **Never use em dashes**, in messages to him or in site copy. The page and code strings currently contain none. Use commas, full stops, colons or brackets.
- Own mistakes plainly and fix them. He values honesty over reassurance, and so does the site.
- Ask before anything hard to undo, anything that changes what the site claims, or any change to the rules that move levels. Visual tweaks he has asked for can go straight ahead.
- He checks results on his phone, iPad and desktop. When he reports a visual problem, reproduce it at his screen width before claiming it is fixed. When a fix is live, tell him to hard-refresh, because his browser may be showing a cached copy.

## 3. The philosophy (these are rules, not preferences)

1. **Never become more reassuring because it knows less.** If a source cannot be read, keep the last confirmed level and say it is "last confirmed", not live. "Unknown" is never shown as "clear". A check only says "none" when it has positively read "none".
2. **Never raise an alarm on weak evidence.** A false High is real harm: it frightens people and spends the site's credibility. Automatic High needs strict, official, current, UK-relevant wording. Level 5 needs a very clear official signal that has held for `hold_hours` (6) and been confirmed recently. See section 7.1 for a real false alarm and what it taught us.
3. **Official sources only move levels.** GOV.UK departments, MI5/GOV.UK terrorism level, the NCSC, the grid operator (via Elexon) and similar. Nothing from the press can move a level or a notice (a safety test proves this). The optional BBC headlines strip (`bbc_headlines` in `site.json`) is display only, links not copies.
4. **Privacy is absolute.** No cookies, no ads, no analytics, no accounts, no tracking. A visitor's household answers stay in their own browser (localStorage) and are never sent anywhere. The page loads **nothing** from any third party: no scripts, fonts, images or styles. The security policy is `default-src 'none'`, and the only outside connections allowed are the two public lookups the visitor's own browser makes if they enter a postcode area: postcodes.io and the Environment Agency. There is also an optional visit counter's own origin, when configured. **System fonts only**: Google Fonts would tell Google about every visitor. Every image or font must be served from `src/` on the site itself.
5. **Licences matter.** Only open-licensed official data is republished, with attribution (see README). Press text is never copied or rewritten for the page. No government logos or names that imply official status.
6. **Low-touch by design.** The site runs itself (`hands_off`, default true): levels and wording come from live data and the rules, around the clock, without anyone awake. Hand-written baseline text is set aside in hands-off mode.
7. **Calm tone.** Plain English, factual, attributed ("X said Y"), no speculation about who did what, no conspiracy or partisan sources.

## 4. Repository map

```
src/index.src.html      The whole page: HTML, CSS and JS in one template (built into site/index.html)
src/*.png|svg|ico|jpg   Icons, social image, and the 1984-mode images (all served from the site itself)
src/sw.js, manifest     Service worker (network-first, saves an offline copy) and web app manifest
tools/build_site.py     Builds site/ from src/, site.json and editorial/ (embeds an offline snapshot)
tools/publish.py        Fetches official sources, applies the rules, writes site/feed.json, state.json,
                        history.xml, and pre-renders the level into index.html. Standard library only.
tools/make_social.py    Makes the social share image
editorial/*.json        Baseline levels, signals, change log, optional notice banner (editor-owned)
official_sources.json   The official feeds read automatically (with notes on ones removed and why)
site.json               Site settings and switches (see below)
tests/test_safety.py    BLOCKING: if any test fails, nothing publishes
tests/test_publish.py   Wider self-checks: warnings only, never blocks publishing
tests/browser_check.py  Real-browser checks of the privacy and safety promises (runs on every push)
.github/workflows/      publish.yml (build and deploy), keepalive.yml (keeps the schedule alive)
```

`site/` and `data/` are generated and git-ignored. Edit `src/` and `editorial/`, never `site/`.

Useful `site.json` switches: `hands_off` (default true), `auto_levels` (false is the emergency brake: stops all automatic raising), `auto_headlines`, `bbc_headlines`, `hold_hours` (6), `stale_days`, `decay_days`, `evidence_window_days` (30), `evidence_min_items` (2), `counter_url` / `counter_sample` (optional private visit counter). The live repo's `site.json` values are the truth; do not "reset" them to template defaults.

## 5. How publishing works

- `publish.yml` runs at 7 and 37 minutes past every hour, on every push to `main`, and on demand. GitHub's scheduler is best-effort and can be late.
- Order: folder sanity check (warnings) > **safety tests (blocking)** > wider self-checks (warnings) > build > fetch sources and write the feed > browser checks (push only) > deploy to GitHub Pages.
- **If `main` fails the safety tests, EVERY run is blocked, including the half-hourly refresh.** The site freezes on its last good build and shows "Out of date" after about 3 hours. Never leave `main` red. If you break it, fix or revert immediately.
- The build job has read-only repo permission. Only `keepalive.yml` can write (an empty commit after 40 quiet days, because GitHub disables schedules after 60). Requiring pull requests on `main` would block that commit.
- Runs are never cancelled half way (that can wedge GitHub Pages).
- **Memory:** each run publishes `state.json` beside `feed.json` and reads the live one back next run (with an Actions cache copy as fallback). It holds the last confirmed terrorism level, official evidence, active triggers, level timers, and the previous levels (for the automatic change log). Losing it never blocks publishing; the site restarts cautiously and says so.
- The automatic change log (`history.xml` RSS and "Changes to the level" on the page) is written whenever a level moves.
- Secrets: `CLOUDFLARE_API_TOKEN` (Actions secret, for Cloudflare Radar internet-outage data). Never print or commit it.

## 6. How to work safely here

1. **Run everything locally before pushing:**
   ```
   python3 -m unittest discover -s tests -p "test_*.py"
   python3 tools/build_site.py && python3 tools/publish.py --editorial-only
   python3 -m http.server 8770 --directory site &  sleep 2 && python3 tests/browser_check.py
   ```
   `test_safety.py` must be fully green. Run the browser check whenever you touch the page.
2. **Never weaken, skip or rewrite a safety test to make code pass.** If a safety test conflicts with a change, stop and explain the conflict to the owner. That rule already caught a bad fix once.
3. Bump `CODE_VERSION` in `tools/publish.py` whenever you change that file. It appears as `build.code` in the live `feed.json`, which is how you confirm what is actually deployed.
4. Small commits, one purpose each. After pushing, watch the Actions run, then **verify the live site**: fetch `https://wary.org.uk/feed.json?cb=<something unique>` (the query string defeats caches) and check `build.code`, `build.commit`, the levels and anything you changed.
5. **Rehearse anything that touches levels, triggers or memory** with `--fixtures DIR` and `--state FILE`: recreate the live state first (run the old code), then run the new code on the same state, and check the transition, the change-log entry, and that nothing repeats on the next run.
6. **Test against complete, real data.** Get the exact source text from the raw API, not from a summarising web-fetch tool (those can silently shorten text; see 7.1). Put the real payload in the test as a fixture and prove the old code fails it.
7. **Check visual work by measurement, not by eye.** Use Playwright: bounding boxes, pixel counts at the element edges, computed styles, at several widths (390, 780, 1024, 1280, 2000). Several "fixed" visual bugs were not fixed because a zoomed screenshot looked fine.
8. Keep the page self-contained: no CDNs, no external fonts, no hotlinked images. New assets go in `src/` and are copied into `site/` automatically by the build.

## 7. Incidents and lessons worth knowing

### 7.1 The false High, 27 to 28 September 2026 (the most important one)
- The Elexon grid check looked for the bare words "demand control". A routine NESO **Electricity Margin Notice** (the lowest grid warning) ends with the stock line "Suppliers please advise ... of any additional Demand Control available". That matched, a 72-hour trigger raised Power to High, and the whole site showed High.
- Fix pack 4 removed the bare phrase but still matched "blackout". The same notice ends with an **Information Note** saying it "does not signal that blackouts are imminent", so the reassurance itself re-triggered High. That was missed because the fix was tested against a copy of the notice that a web-fetch tool had shortened.
- Fix pack 5 (live now) **trusts Elexon's structured `warningType` first**: an escalation type (High Risk of Demand Reduction, Demand Control Imminent, and so on) alerts; a routine type (Electricity Margin Notice, Capacity Market Notice, NRAPM, information) is routine whatever its prose says; only a notice with no type is judged on its text, and then only on named escalation stages and explicit instructions. See `grid_is_escalation()` and `tests/test_publish.py::GridFalseAlarm` (which holds the complete real notice).
- **Stored triggers are durable by design:** once accepted, a trigger stays until its own expiry even if the source changes or fails. So fixing a matcher does NOT clear a false trigger already in memory. The tool for that is `RETIRED_TRIGGER_RULES` in `publish.py`: give the corrected rule a new name, retire the old one with a plain-English explanation, and the stored trigger is dropped on the next run and a "Correction." entry is written to the change log once. Retired so far: `grid-alert`, `grid-demand-control`. The current rule is `grid-escalation`.
- General lesson: word-searching official prose is fragile, because routine notices contain alarming words in reassuring or boilerplate sentences. Prefer structured fields; match the shape of a real escalation, not individual words.

### 7.2 Other rules with history
- **Terrorism level:** read from GOV.UK first, MI5's page as back-up (MI5 refuses GitHub's servers with HTTP 403). It is accepted only from a clear "national threat level is ..." sentence and only if it is within one step of the last confirmed level, so a page redesign cannot create a false Critical. The official CRITICAL shows as High until it has held for `hold_hours`.
- **Official evidence:** two or more different official statements in 30 days naming hostile or state-linked activity in an area raise it to Elevated; it falls back as they age out.
- **Removed feeds:** the Drinking Water Inspectorate GOV.UK feed (withdrawn, HTTP 410) and CISA (refuses automated requests). Notes are in `official_sources.json`.
- The module docstring at the top of `publish.py` is out of date (it predates hands-off mode and automatic levels). The code and README are right.

### 7.3 `tests/test_publish.py` is stale (first job to pick up)
The repo's copy is essentially the pack-12 version plus the `GridFalseAlarm` class, so about 7 older tests fail against today's code (in `Workflow`, `Build`, `LowTouch`, `Cables`). These failures are pre-existing, sit in the warnings-only step, and do not block publishing, but they hide real regressions. Bring the file up to date with the current code so the step goes fully green. Do **not** reintroduce the old test that read a file outside the repo (`../KEEP-PRIVATE/...`); it can never pass in CI.

### 7.4 Page and CSS gotchas
- **Themes:** `applyDisplay()` sets `data-theme` generically. Any `prefers-color-scheme: dark` rule must exclude every explicit theme (`:not([data-theme="light"]):not([data-theme="1983"])`), or dark mode leaks into other themes.
- **Calm view** (`data-view="calm"`) repoints `--hero-bg`, `--hero-fg` and `--hero-dim` at the root to light-background colours. Any component that keeps a dark background in some theme must pin its own text colours, or you get dark text on dark. This bit the 1984-mode header, hero and footer.
- **SVG:** give standalone SVGs explicit `width`/`height` matching the viewBox (Safari otherwise sizes them oddly); make the viewBox include stroke width (strokes paint half their width beyond the path); and avoid `<symbol>` + `<use>` with a viewBox that does not start at 0,0, because the symbol's viewport is placed at 0,0 of the outer SVG and gets offset and clipped. Draw it inline instead.
- **Images that should only load in one theme:** use CSS `background-image` under that theme's selector. An `<img>` downloads even when hidden.
- The service worker is network-first, so changes appear after a reload, but the owner may need a hard refresh.

## 8. 1984 mode (a display easter egg)

A theme in the Display dialog, labelled **"1984 mode"**, styled after the 1980 "Protect and Survive" booklet: burnt orange and black on cream paper, condensed uppercase headings, paper grain, a burning London skyline behind the hero (St Paul's on the right), six booklet-style illustrations, and a gradient footer. It is aimed at the owner's Threads-fan audience.

- **Skin only. It must never change wording, numbers, levels or ratings.** Decorative extras are `aria-hidden`. `Safety1983Mode` in `test_safety.py` guards this and much of what follows.
- The internal value is `1983` (`data-theme="1983"`, asset names `1983-*`); only the visible label says 1984. Keep the internal value, because visitors' saved settings use it.
- Mutually exclusive with system/light/dark. All assets are local; the illustrations are CSS backgrounds, so they are never downloaded in other themes. The illustrations are hidden in calm view and in print, and the fridge-sheet printout is unchanged.
- The centred house-and-rings mark is hidden below 780px wide on purpose: the header text wraps and there is no clear space for it.
- Paper grain: `src/1983-paper.png`, multiply blend, opacity 0.4 (the owner's choice). Two earlier grain attempts (an SVG turbulence filter that was invisible in some browsers, and a dot halftone that obscured the page) were dropped, and a test stops them coming back.

## 9. Ideas and open threads (ask before starting any)
- Bring `test_publish.py` up to date (7.3). Recommended first.
- Natural Resources Wales flood warnings (the Environment Agency API covers England only).
- Earlier ideas, deliberately parked: an embeddable status badge for other sites, a public change-history page, and a private "your Wary story" view.
- Not decided: whether a real Electricity Margin Notice should nudge Power to Elevated. Today it is shown as a routine notice only, which matches the grid operator's own framing. Do not change this without the owner.
- AI-written summaries were left out on purpose (press terms, and a new way to be wrong). If ever added: official text only, names and numbers checked against the source, clearly labelled, with a spending cap.
