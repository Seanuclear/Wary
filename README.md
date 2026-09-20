# Wary

**Watchful, not worried.** Calm, sourced UK household threat levels.

Live site: https://wary.org.uk

Wary shows one household level from 1 to 5 for the UK, the six areas behind it, live checks from official sources, and a checklist that adapts to each visitor's household.

**It is independent, non-commercial and not official.** No government body runs or endorses it. It does not replace Emergency Alerts, the emergency services, your council or the BBC. It can be wrong or late.

## Privacy

No cookies. No ads. No tracking. There are no accounts. A visitor's answers stay in their own browser and are never sent here. If a visitor adds the first half of a postcode, their own browser asks postcodes.io and the Environment Agency about that area directly. The site loads no third-party scripts, fonts or images, and its content security policy tells the browser to refuse them.

## How it works

A static site, rebuilt by GitHub Actions every 30 minutes and whenever a file changes.

- `editorial/` holds the editor's baseline levels, change log, notices and signals (short summaries in the editor's own words, each linking to the original).
- Official, open-licensed sources are read automatically: the MI5 threat level, GOV.UK, the NCSC, the Met Office, GOV.UK Emergency Alerts and Elexon grid notices.
- Press feeds are not used. Nothing from the press can move a level.
- `src/` is the page. `tools/` builds it. `tests/` run before every publish.

## Sources and attribution

- Contains public sector information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/): GOV.UK, the NCSC, the Met Office and GOV.UK Emergency Alerts.
- This uses Environment Agency flood and river level data from the real-time data API (Beta).
- Contains BMRS data (c) Elexon Limited copyright and database right.
- Postcode areas via postcodes.io. Contains Ordnance Survey data (c) Crown copyright and database right 2025. Contains Royal Mail data (c) Royal Mail copyright and database right 2025. Contains National Statistics data (c) Crown copyright and database right 2025. Contains NRS data (c) Crown copyright and database right 2025.
- News reports are linked, not copied. Their owners keep their copyright.

## Corrections

Errors are welcome. The contact address is on the website, in the About section.
