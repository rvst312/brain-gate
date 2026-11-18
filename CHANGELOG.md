# Changelog

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
