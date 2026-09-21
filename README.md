# Wary

**Watchful, not worried.** Calm, sourced UK household threat levels.

Live site: https://wary.org.uk

Wary shows one household level from 1 to 5 for the UK, the six areas behind it, live checks from official sources, and a checklist that adapts to each visitor's household.

**It is independent, non-commercial and not official.** No government body runs or endorses it. It does not replace Emergency Alerts, the emergency services, your council or the BBC. It can be wrong or late.

## Privacy

No cookies. No ads. No tracking. There are no accounts. A visitor's answers stay in their own browser and are never sent here. If a visitor adds the first half of a postcode, their own browser asks postcodes.io and the Environment Agency about that area directly. The site loads no third-party scripts, fonts or images, and its content security policy tells the browser to refuse them.

## How it works

The levels and the change log are calculated automatically from official data. The editor maintains the rules and is responsible for what the site says.

A static site, rebuilt by GitHub Actions every 30 minutes and whenever a file changes.

**A guiding rule:** the site must never become more reassuring merely because it knows less. If a source cannot be read, it keeps the last confirmed level, says so, and shows when it was last confirmed. It also never raises an alarm on weak evidence: automatic High needs strict, official, current, UK-relevant wording, and Critical needs a signal that has held for hours and been confirmed recently.

**Memory:** each run publishes a small `state.json` beside `feed.json` and reads the previous one back next time, so the site remembers recent statements and timers without any database. A copy is also kept in the workflow cache as a fallback. A missing or damaged memory never stops publishing. It restarts cautiously and says so on the page.

**Safety checks block publishing:** `tests/test_safety.py` runs before every build. If it fails, nothing is published, the last good version stays live, and the page itself says "Out of date" after about 3 hours. Browser checks (`tests/browser_check.py`) also run whenever the code changes, and cover the privacy promises.

- `editorial/` holds the editor's baseline levels, change log, notices and signals (short summaries in the editor's own words, each linking to the original).
- Official, open-licensed sources are read automatically: the national terrorism threat level (from GOV.UK, with MI5's page as a back-up), GOV.UK, the NCSC, the Met Office, GOV.UK Emergency Alerts, Elexon grid notices and space weather levels (NOAA).
- Areas rise to Elevated by themselves when two or more different official statements in 30 days name hostile activity affecting them, and fall back as the statements age out. Terrorism follows the MI5 level.
- Press feeds are not used, apart from an optional strip of BBC headlines and links. It is a switch in `site.json` (`bbc_headlines`): off in the template, and currently ON for wary.org.uk. Headlines are only ever displayed. Nothing from the press can move a level, and a test proves it.
- `src/` is the page. `tools/` builds it. `tests/` run before every publish.

## Sources and attribution

- Contains public sector information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/): GOV.UK, the NCSC, the Met Office and GOV.UK Emergency Alerts.
- This uses Environment Agency flood and river level data from the real-time data API (Beta).
- Contains BMRS data (c) Elexon Limited copyright and database right.
- Optionally, UK internet disruptions from Cloudflare Radar (data licensed CC BY-NC 4.0), shown only for information.
- Space weather storm levels from the NOAA Space Weather Prediction Center (US government data). UK space weather forecasts come from the Met Office.
- Postcode areas via postcodes.io. Contains Ordnance Survey data (c) Crown copyright and database right 2025. Contains Royal Mail data (c) Royal Mail copyright and database right 2025. Contains National Statistics data (c) Crown copyright and database right 2025. Contains NRS data (c) Crown copyright and database right 2025.
- News reports are linked, not copied. Their owners keep their copyright.

## Corrections

Errors are welcome. The contact address is on the website, in the About section.

## Reporting a security problem

See `SECURITY.md`. Please do not put details of a vulnerability in a public issue.

## Reuse

The source code is published so that anyone can check how Wary works and where its numbers come from. No licence for reuse has been granted yet: all rights are reserved for now. If you would like to reuse or adapt it, please ask using the contact address on the website. Official data shown on the site remains under its own open licences, listed under Sources and attribution above.
