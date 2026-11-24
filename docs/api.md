# HTTP API

Private service. Expose it only on your internal network.

```bash
export BRAIN_GATE_INTEGRATIONS='{"my-gateway": {"token_sha256": "<sha256 of token>", "policies": ["brain-external-es"]}}'
uv run braingate serve --host 127.0.0.1 --port 8088      # uses models/ automatically if present
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BRAIN_GATE_INTEGRATIONS` | `{}` | Integrations: id → SHA-256 of the bearer token and the allowed policies. Tokens are never stored in plain text. |
| `BRAIN_GATE_GLINER_PATH` | unset | Local weights directory. If set and loading fails, the service never becomes ready. |
| `BRAIN_GATE_GLINER_THRESHOLD` | `0.4` | Detector cutoff. |
| `BRAIN_GATE_POLICY_DIR` | built-in | Directory of policy JSON files. |
| `BRAIN_GATE_MAX_CONCURRENCY` | `4` | Concurrent inferences per process. |
| `BRAIN_GATE_TIMEOUT_S` | `5` | Per-request processing timeout. |
| `BRAIN_GATE_TOKEN_KEY` | random per process | Key for reversible tokens. Set it to get the same tokens across restarts and workers. Treat it as a secret. |

## `POST /v1/mask`

Request:

```json
{ "text": "María López pidió que le escribieran a maria@example.com",
  "policy_id": "brain-external-es",
  "include_spans": false }
```

Header: `Authorization: Bearer <token>`.

### `200 masked`

Processing is complete. With no detections, `masked_text` equals the input.

```json
{ "status": "masked",
  "masked_text": "[PERSONA] pidió que le escribieran a [EMAIL]",
  "policy_version": "brain-external-es@3",
  "detector_version": "rules@1+gliner-pii@1" }
```

With `include_spans: true`, the response adds categories and positions on the **original** text. Offsets are Unicode
code points, `start` inclusive and `end` exclusive. No detected text is ever returned:

```json
"spans": [ {"start": 0, "end": 11, "category": "PERSONA", "action": "mask"},
           {"start": 39, "end": 56, "category": "EMAIL", "action": "mask"} ]
```

### Reversible tokens

With `"reversible": true`, categories the policy marks as [reversible](policies.md#reversible-tokens) are replaced by
unique tokens and the response adds `mappings`, the table needed to rebuild the data:

```json
{ "text": "María López pidió que le escribieran a maria@example.com",
  "policy_id": "brain-external-es-reversible", "reversible": true }
```

```json
{ "status": "masked",
  "masked_text": "[PERSONA_5c0e9a41d7] pidió que le escribieran a [EMAIL_bb25739858]",
  "mappings": { "[PERSONA_5c0e9a41d7]": "María López", "[EMAIL_bb25739858]": "maria@example.com" },
  "policy_version": "brain-external-es-reversible@1",
  "detector_version": "rules@1+gliner-pii@1" }
```

`mappings` contains only fragments of the text the caller just sent, and only when requested. The service stores
nothing: keep the table in memory for the duration of your request and replace the tokens in the provider's answer.
`HttpMasker` and the gateway do this for you. `mappings` is `{}` when the policy has no reversible categories, and is
omitted on `needs_review`. It never appears in logs or errors.

### `200 needs_review`

The policy requires review. `masked_text` is omitted.

```json
{ "status": "needs_review", "policy_version": "brain-external-es@3", "detector_version": "rules@1+gliner-pii@1" }
```

### Errors

Errors never include the original or partial text.

| HTTP | `error` | Cause |
|---|---|---|
| 401 | `unauthorized` | Missing or unknown token |
| 403 | `policy_forbidden` | Policy unknown or not allowed for this integration |
| 413 | `too_large` | `text` over the policy's `max_chars` |
| 422 | `invalid_request` | Malformed body (the input is **not** echoed back) |
| 500 | `processing_error` | Detector failure |
| 503 | `not_ready` | Model still loading |
| 504 | `timeout` | Over `BRAIN_GATE_TIMEOUT_S` |

## `GET /readyz`, `GET /healthz`

`/readyz` returns `{"status": "ready", "detector_version": "..."}` once the model is loaded, or 503 before that.
`/healthz` is a liveness check.

## CLI

```bash
braingate mask "texto"                 # stdin works too; exit 3 on needs_review, 2 on unknown policy
braingate mask --spans "texto"         # JSON with categories and positions
braingate mask --gliner none "texto"   # rules only
braingate serve --port 8088
braingate download-model --dest models/gliner_multi_pii-v1
```
