# Contributing to HA Creality WS

Thanks for helping. This is a Home Assistant integration for Creality printers over their local WebSocket, plus two Lovelace cards (`k_printer_card.js`, `k_cfs_card.js`). By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

- [Questions](#questions)
- [Issues](#issues)
- [Contributor PR Flow (Non-Translation PRs)](#contributor-pr-flow-non-translation-prs)
- [Translations](#translations)
- [Development setup](#development-setup)
- [House rules](#house-rules)
- [Commits and changelog](#commits-and-changelog)
- [License](#license)

## Questions

Ask in [GitHub Discussions](https://github.com/3dg1luk43/ha_creality_ws/discussions). Issues are for bugs, feature requests and documentation problems.

## Issues

Every issue goes through a form: [bug report](https://github.com/3dg1luk43/ha_creality_ws/issues/new?template=bug_report.yml), [feature request](https://github.com/3dg1luk43/ha_creality_ws/issues/new?template=feature_request.yml) or [documentation](https://github.com/3dg1luk43/ha_creality_ws/issues/new?template=documentation.yml). Blank issues are disabled, and an issue opened without a template is closed automatically.

A bug report needs:

- the integration version and the Home Assistant version
- the printer model (and firmware version, if you know it)
- logs showing the error, or the printer's **Download diagnostics** file (Settings > Devices & services > the printer's menu), or the `printers` section of a `ha_creality_ws.diagnostic_dump` response (Developer Tools > Actions)

Reproduce on the latest release first, on a supported Home Assistant version (the minimum is `homeassistant` in `hacs.json`); a report against an older version is asked to update. An incomplete bug report is labelled `more info required`, gets a reminder after 2 days and is closed after 5. Reply with a comment and it moves to `awaiting maintainer review`. A maintainer can clear the check with a `/fine` comment.

After a release, each issue it references gets a comment and the `done` label, and closes after 5 days without activity. Comment if the fix does not work for you.

## Contributor PR Flow (Non-Translation PRs)

Agree the work with the maintainer before you write it. There is often unreleased work in progress that the tracker does not show, and a surprise PR that duplicates it, conflicts with it or takes an approach that will not be merged wastes your time as much as anyone's. Bug fixes, features, refactors, docs and tests all go through this flow, which GitHub Actions enforce:

1. **Open an issue** with one of the templates above.
2. **Tick the PR-intent box** at the bottom of the form (**Contributing a Fix**, **Contributing an Implementation**, or the documentation form's equivalent).
3. **Wait for the `accepted` label.** Only the maintainer can apply it; a workflow removes it if anyone else does. Use the issue to settle scope and approach.
4. **Open the PR**, fill in the template, and link the issue with `Closes #NNN`.

Keep a PR to one change, add or update tests, and add a [changelog](#commits-and-changelog) entry for anything users will notice.

### What gets a PR closed automatically

- **PR template deleted entirely:** closed immediately.
- **Description or Type of Change left unfilled:** labelled `needs description`, closed after 5 days unless filled in.
- **No linked issue:** labelled `needs accepted issue`, closed after 3 days.
- **Linked issue not accepted yet:** labelled `awaiting maintainer` and parked, not closed. Opening the PR alongside a fresh issue does not speed anything up; the review starts once the issue is accepted.

The maintainer ([@3dg1luk43](https://github.com/3dg1luk43)) and bots are exempt.

## Translations

Translation PRs skip the accepted-issue requirement. A PR counts as one when it touches only:

- `custom_components/ha_creality_ws/translations/`
- `custom_components/ha_creality_ws/strings.json`
- `custom_components/ha_creality_ws/www/i18n/`

It still fills in a PR template. Use the translation one by adding `template=translation.md` to the compare URL, e.g. `.../compare/main...your-branch?expand=1&template=translation.md` (source: `.github/PULL_REQUEST_TEMPLATE/translation.md`).

There are two layers: `translations/<lang>.json` for the integration (setup, options, entities, notifications) and `www/i18n/<lang>.json` for the cards. `strings.json` is the English source, and `translations/en.json` must match it. For a new language, copy both English files and translate them.

Rules:

- Every locale carries every key that English has.
- Every `{placeholder}` keeps exactly the name it has in English. A renamed one renders as empty text at runtime, with only a warning in the log.
- User-visible strings never go inline in `.py` or `.js`; they live in these files.
- No machine translation. Translate as someone who knows 3D printing: "bed" is the print bed, "chamber" the enclosure, "flow" the extrusion flow.

`python -m pytest tools/tests/test_translations.py` checks the integration layer. Changing the English wording of a card string is not translation-only, because the cards bundle an English fallback that the tests compare against `www/i18n/en.json`.

## Development setup

Python 3.11 or 3.13; CI runs the suite on both.

```bash
git clone https://github.com/YOUR_USERNAME/ha_creality_ws.git
cd ha_creality_ws
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r tools/requirements.txt
python -m pytest
```

`pyproject.toml` points pytest at `tools/tests`. The card tests in `tools/tests/js/` are driven from pytest and need `node` on PATH; without it they are skipped, so install node before touching either card.

### No printer needed

`tools/creality_printer_test_server.py` emulates K1, K2, Ender 3 V3 and Hi printers: WebSocket telemetry on port 9999, MJPEG or WebRTC video on port 8000, fans, CFS material writes, and test-control endpoints that pin a scenario instantly. Run it without arguments for the full help, and see [tools/README.md](tools/README.md).

```bash
python3 tools/creality_printer_test_server.py --model k2plus --simulate-print --deterministic
```

Add the simulator's host to a development Home Assistant as you would a real printer.

### Before asking for review

Run `python -m pytest`, and `tools/release_check.sh`, the preflight the maintainer runs before each release. Fixing what it flags now saves a review round.

## House rules

- **Local only.** No cloud calls and no cloud-backed dependencies; a test rejects unexpected external URLs. Updates stay push-driven, not polled.
- **No em dash (U+2014) anywhere in the repo**: code, comments, docs, translations. `tools/tests/test_code_hygiene.py` fails on one. Use `-`, `--`, a colon or a comma.
- **Mixed CRLF/LF line endings**, and no `.gitattributes`. Do not let your editor normalise or reformat whole files; a diff that rewrites every line will be sent back.
- **No formatter or linter is configured.** Match the surrounding code and leave lines you did not change alone.
- **Async only.** Never block the event loop.
- **Keep identifiers stable**: entity unique IDs, option keys and card config keys. Dashboards and automations depend on them.
- **No fabricated data.** Entities and placeholders expose what the printer or its sliced file actually reports. A feature that needs a value nothing sends will be declined.

Design notes for the coordinator, client, entities and cards are in [.github/copilot-instructions.md](.github/copilot-instructions.md).

## Commits and changelog

Conventional prefixes (`fix:`, `feat:`, `docs:`, `refactor:`, `test:`, `chore:`), then a lowercase summary of what changed, as in `git log`:

```text
fix: stop a rejected camera submit being saved by the next section
feat: add filament estimate sensors
```

User-visible changes get an entry in [CHANGELOG.md](CHANGELOG.md) (Keep a Changelog style) under the top `## X.Y.Z - Unreleased` heading, which the maintainer dates at release. Link the issue, and credit yourself if you like. Refactors and test-only changes need no entry.

## License

The project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0), and so are your contributions to it. You keep the copyright in your own work.
