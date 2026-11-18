# Contributing to BrainGate

Thanks for helping! A few ground rules keep the project safe to use with sensitive data.

## Setup

```bash
uv sync --extra gliner --extra server
uv run braingate download-model   # optional: needed for tests/test_gliner_real.py
uv run pytest
```

## Rules for changes

1. **Fail closed.** New request fields, part types or routes are blocked until the gateway explicitly walks them.
2. **No content in logs, errors or exceptions.** Add telemetry fields only to the whitelist in `telemetry.py`, and never log text, spans or payloads.
3. **No network at inference time.** Only `models.py` may download, and only when the user explicitly asks for it.
4. **Policies are versioned.** Any change to a policy file bumps its `version`.
5. **Test the failure path.** Every new detector, field or route needs a test showing that the mock provider receives nothing when processing fails.
6. **Use fake data only** in tests, examples and issues: `example.com` addresses, the published test IBAN `ES91 2100 0418 4502 0005 1332`, and DNIs built with the check-letter formula.

## Adding a detector

Implement `name`, `version` and `detect(text) -> list[Span]` (code-point offsets, `end` exclusive), then pass it to
`Engine([...])`. Map its categories in the policy and add them to `priority`.

## Pull requests

Keep PRs focused, include tests, and describe behavioural changes in `CHANGELOG.md`.
