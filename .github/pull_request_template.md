> [!CAUTION]
> **Do not open this PR unless your linked issue already carries the `accepted` label.**
>
> Open an [issue](https://github.com/3dg1luk43/ha_creality_ws/issues/new/choose) first, tick "I plan
> to submit a PR", and wait for the `accepted` label so the scope is agreed before you write anything.
>
> Closed automatically: PRs linking no issue, and PRs with this template deleted or left unfilled.
> **Translation PRs** (only `translations/`, `www/i18n/` and `strings.json`) are exempt from the
> accepted-issue requirement, but still fill this template in.

Closes #<!-- must already have the `accepted` label -->

## Description

<!-- What problem does this solve, and how? -->

## Type of Change

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Docs
- [ ] Tests
- [ ] Card / UI
- [ ] Translation

## Testing

<!-- How did you verify this? For a bug fix: printer model, integration and Home Assistant version. -->

- [ ] `python -m pytest` passes
- [ ] Tests added or updated
- [ ] Verified on a real printer (model: ) or against `tools/creality_printer_test_server.py`

## Checklist

- [ ] The linked issue already had the `accepted` label (or this is a translation PR)
- [ ] No hardcoded user-facing strings: they live in `strings.json` / `translations/` / `www/i18n/`, in every locale
- [ ] `CHANGELOG.md` updated under the top (`Unreleased`) entry if the change is user-visible
- [ ] Breaking change? Describe it and the migration path here:

## Notes for reviewers

<!-- Anything else worth knowing. Screenshots welcome for card changes. -->
