# Architecture

BrainGate protects a company "brain" (a RAG assistant, copilot or knowledge base) at the two points where
personal data can escape: when content is **indexed**, and when a request **leaves** for an external provider.

## Where to apply masking

| Control point | What it protects | Cost and effect on search |
|---|---|---|
| **Before indexing** | Stored chunks, metadata and embedding inputs. If embeddings are computed by an external API, masking runs before that call. | Paid once per document version. Searches that rely on exact names or IDs degrade. |
| **After retrieval, before sending** | Everything leaving the brain: retrieved chunks, history, tool results. | Works on an existing index with no rebuild. Adds per-request latency. Originals stay in the index and its copies. |
| **Both** | Less sensitive data at rest, plus a re-check of exactly what is sent. | Two controls. Ingest processes whole documents; the gateway processes only the selected context. |

**Recommended migration for a brain that is already live:**

1. **Inventory.** Find out what the index, metadata and backups contain, who can access them, and whether embeddings were generated locally or externally.
2. **Protect egress first.** Put the gateway in front of every external call (chat, embeddings, tools, any direct integration). Validate against `MockProvider`, then confirm that a masker failure blocks the call.
3. **Build a masked index in parallel** from the originals. Record changes made after the rebuild starts and apply them before cutover.
4. **Compare and switch.** Run the same queries on both indexes, check latency, and switch reads reversibly. Keep the egress control during any rollback.
5. **Close.** Decide whether to retire or retain the old index and its replicas. Onboard a second brain through identity and policy only, without forking the engine.

## Components

```
src/brain_gate/
├── rules.py        Regex detectors + checksum validators (DNI, NIE, IBAN), email, phones
├── contextual.py   GLiNER adapter: offline load, overlapping sentence-aware windows
├── engine.py       Merge → resolve overlaps → decide → apply
├── policy.py       Versioned policy model + store (built-in policies in policies/)
├── api.py          FastAPI app: POST /v1/mask, /readyz, /healthz
├── gateway.py      Egress gateway, request walkers, maskers (local / HTTP), MockProvider
├── vault.py        Reversible tokens: per-request table token → original, response restore
├── telemetry.py    Whitelisted, content-free structured logs
├── models.py       Pinned weight download (the only code that touches the network)
└── cli.py          braingate mask | serve | download-model
```

## Detection pipeline

### 1. Rules (`rules.py`)

| Category | Pattern | Validation |
|---|---|---|
| `EMAIL` | RFC-ish local part + domain + TLD | — |
| `TELEFONO` | Spanish 9-digit numbers starting with 6/7/8/9, optional `+34`/`0034`, any separators; international `+CC` numbers | 9–15 digits |
| `DNI` | 8 digits + letter, optional `-`/space | Letter = `TRWAGMYFPDXBNJZSQVHLCKE[n % 23]` |
| `NIE` | `X/Y/Z` + 7 digits + letter | Same, with X→0, Y→1, Z→2 |
| `IBAN` | Country + 2 check digits + groups of 4 | ISO 13616 mod-97, per-country length |

A candidate with the right shape and a **wrong checksum is not dropped**: it is returned with score 0.5, so the
policy decides (by default these go to review: a DNI-shaped number with a bad letter is suspicious either way).

### 2. Contextual model (`contextual.py`)

`GlinerDetector` wraps [GLiNER Multi PII v1](https://huggingface.co/urchade/gliner_multi_pii-v1) (mDeBERTa-v3 backbone, Apache 2.0).

- **Offline by construction.** Sets `HF_HUB_OFFLINE=1`, loads with `local_files_only=True`, and points the backbone
  tokenizer at `models/.../backbone/` instead of the Hub id. A test patches `socket.connect` during load and fails on any attempt.
- **Windows.** Text longer than `window_size` (1,200 chars) is split into overlapping windows (200 chars), cut at
  the last sentence boundary in the second half of the window, never mid-word. Any entity shorter than the overlap appears
  whole in at least one window. Offsets are shifted back onto the original text.
- **Labels.** `person` / `full name` → `PERSONA`, `address` / `street address` → `DIRECCION`.
- **Threshold.** `BRAIN_GATE_GLINER_THRESHOLD` (default 0.4). Spans between this value and the category's `min_score` are *doubtful*.

Any object with GLiNER's `predict_entities(text, labels, threshold)` signature can be plugged in, which is how the
unit tests exercise windowing without weights.

### 3. Merge and resolve (`engine.py`)

1. **Same category:** overlapping or touching spans are merged into their union with the max score (detector duplicates, entities split between windows).
2. **Across categories:** sorted by policy priority, then length, then score. When a lower-priority span overlaps a winner, the winner **extends to the union**. Over-masking is preferred to leaking a fragment.

### 4. Decide and apply

For each span: literal exception → `keep`; category action `keep` → `keep`; `score < min_score` → `on_doubt`
(category first, then policy); otherwise the category action. If any span is `review`, the result is
`needs_review` and **no text is returned**. Otherwise placeholders replace the masked spans in the original string.
When the caller passes a `Vault` and the policy marks the category `reversible`, the span is replaced by a unique
token instead (see [Data reconstruction](#data-reconstruction-vaultpy)).

## Egress gateway (`gateway.py`)

```python
gateway = Gateway(integration_id, masker, providers={"llm": provider},
                  policy_map={("my-brain", "llm"): "brain-external-es"})
result = gateway.send(brain="my-brain", destination="llm", route="chat", request=payload)
```

- **Policy selection** is by `(brain, destination)` for the gateway's integration identity. The masker (local or HTTP) checks the integration may use that policy.
- **Field walking** (allowlist, fail-closed):
  - `chat`: `system` (string or text parts), `messages[].content` (string or parts). Parts are `text`, `tool_use` (every string leaf of `input`) and `tool_result` (string or text parts). Passthrough top-level keys: `model, max_tokens, temperature, top_p, stop, tools, tool_choice, stream`.
  - `embeddings`: `input` (string or list of strings), `model`, `dimensions`.
  - Anything else blocks the request: images, documents, unknown part types, unknown keys, other roles, other routes.
- **Atomicity.** The request is deep-copied, every field is masked, and only then does `provider.send` run. The first `needs_review` or error aborts with a `GatewayResult(status="blocked", reason=...)`.

| Block reason | Cause |
|---|---|
| `needs_review` | Policy requires review for some span |
| `detector_failure`, `masker_unreachable`, `timeout`, `processing_error`, `invalid_mappings` | Masker problems |
| `too_large` | Field over `policy.max_chars` or request over `max_request_chars` |
| `unsupported_part`, `unsupported_field`, `unsupported_message`, `unsupported_content`, `unsupported_input`, `unsupported_route`, `missing_messages` | Format with no defined treatment |
| `no_policy`, `unknown_destination`, `policy_forbidden` | Configuration / permission |

## Data reconstruction (`vault.py`)

```
"Escribe a maria@example.com"  ──mask──►  "Escribe a [EMAIL_bb25739858]"  ──►  LLM
                                   │ Vault (memory, this request only)
"Enviado a maria@example.com"  ◄─restore─  "Enviado a [EMAIL_bb25739858]"  ◄──  LLM
```

- **Token.** `[CATEGORY_<10 hex>]`, where the hex is `HMAC-SHA256(key, category + "\0" + normalized value)`. Keyed, so
  low-entropy identifiers cannot be brute-forced; deterministic, so the same value gets the same token in every
  field and every turn (history that is re-masked on each request stays coherent and provider-side prompt caching
  keeps working). On a collision between two different values the token grows by two hex digits.
- **Scope.** `Gateway.send` creates one `Vault` per `chat` request, passes it to every masker call, restores the
  response and drops it. Nothing is written to disk, logs or telemetry (only the `restored` count is logged).
  The key lives for the lifetime of the `Gateway` (random unless `token_key` is given).
- **Restore.** Every string leaf of the provider response is scanned, including tool-call arguments, so local tools
  receive real values. Tokens are matched with brackets or bare (`EMAIL_bb25739858`, for models that drop them).
  A token the table doesn't know, or one the model altered, is left as is: the output shows a token, never a wrong value.
- **HTTP masker.** The service stays stateless: it returns `mappings` per call and `HttpMasker` merges them into the
  request's vault. Missing or malformed mappings, or a token arriving with two different values, block the request
  (`invalid_mappings`).
- **Not covered.** Streaming responses (a token can be split across chunks), persistent tables, and restoring
  embeddings or index content: those keep fixed placeholders.

## Exact-identifier lookup

A masked general index cannot find "María López" or "12345678Z", by design. If an authorized flow needs to find a
case by email, DNI or another ID, serve it from a **restricted local lookup over the originals** that enforces the
document's permissions before returning anything. Keep names, emails and IDs out of general index metadata when an
opaque internal reference is enough (see `examples/02_index_masking.py`).

## Operations

- The model loads **once per process**. `/readyz` returns 503 until it is ready. If `BRAIN_GATE_GLINER_PATH` is set and loading fails, the service does not become ready: there is no silent fallback to rules only.
- Limits: `policy.max_chars`, `BRAIN_GATE_MAX_CONCURRENCY` (4), `BRAIN_GATE_TIMEOUT_S` (5). Chat runs synchronously. Use a resumable batch job for re-indexing.
- Logs contain `event, integration, brain, destination, policy_version, detector_version, status, reason, elapsed_ms, chars, fields, counts, restored`, and nothing else. `telemetry.emit` raises on any other field.
- FastAPI's default 422 handler echoes the request body. BrainGate replaces it, and the 500 handler, with bodies that never contain input.
