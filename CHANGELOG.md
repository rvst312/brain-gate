# Changelog

## 0.2.0

Data reconstruction (reversible tokens).

- Policies can mark categories as `reversible` (policy-wide default plus per-category override). Reversible values are
  replaced by a unique keyed token such as `[DNI_7f3a9c2e41]` instead of a fixed placeholder, so the model can tell
  two people apart and refer back to them.
- New `Vault`: per-request, in-memory table `token -> original`. Tokens are HMAC-SHA256 of the normalized value, so the
  same value always gets the same token under the same key and cannot be brute-forced without it.
- `Gateway.send` restores the provider response on the `chat` route, including tool-call arguments, and reports
  `GatewayResult.restored`. New options `token_key` and `restore`.
- `POST /v1/mask` accepts `reversible: true` and returns `mappings`. New variable `BRAIN_GATE_TOKEN_KEY`.
- New built-in policy `brain-external-es-reversible@1` and example `04_reversible_chat.py`.
- Breaking for custom maskers: `Masker.mask` takes an optional third argument `vault`.

## 0.1.0

First public release.

- Rule detectors with checksum validation: DNI, NIE, IBAN; Spanish and international phones; email.
- GLiNER Multi PII v1 adapter: offline loading, overlapping sentence-aware windows.
- Engine: same-category merge, priority-based overlap resolution with union growth, per-category doubt handling.
- Policy `brain-external-es@3` (doubtful names and addresses masked, identifiers sent to review, 25k character limit).
- Private API `POST /v1/mask` with per-integration tokens, policy permissions, size, concurrency and time limits, and content-free errors.
- Fail-closed egress gateway for chat and embeddings, with local and HTTP maskers and a mock provider.
- `braingate` CLI: `mask`, `serve`, `download-model`.
- Examples: RAG chat egress, masked indexing, HTTP service.
