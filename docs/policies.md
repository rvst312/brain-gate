# Policies

A policy is a versioned JSON file that tells the engine **what** to look for and **what to do** with each finding.
The engine applies it. Deciding whether a policy is adequate for a given data treatment is the responsibility of
whoever defines and approves it.

Built-in policies live in [`src/brain_gate/policies/`](../src/brain_gate/policies). Point `BRAIN_GATE_POLICY_DIR` at
your own directory to replace them.

## Reference: `brain-external-es@3`

```json
{
  "id": "brain-external-es",
  "version": 3,
  "categories": {
    "IBAN":      { "action": "mask", "placeholder": "[IBAN]",      "min_score": 0.9 },
    "NIE":       { "action": "mask", "placeholder": "[NIE]",       "min_score": 0.9 },
    "DNI":       { "action": "mask", "placeholder": "[DNI]",       "min_score": 0.9 },
    "EMAIL":     { "action": "mask", "placeholder": "[EMAIL]",     "min_score": 0.5 },
    "TELEFONO":  { "action": "mask", "placeholder": "[TELEFONO]",  "min_score": 0.5 },
    "PERSONA":   { "action": "mask", "placeholder": "[PERSONA]",   "min_score": 0.5, "on_doubt": "mask" },
    "DIRECCION": { "action": "mask", "placeholder": "[DIRECCION]", "min_score": 0.5, "on_doubt": "mask" }
  },
  "priority": ["IBAN", "NIE", "DNI", "EMAIL", "TELEFONO", "DIRECCION", "PERSONA"],
  "on_doubt": "review",
  "on_unknown_category": "mask",
  "max_chars": 25000
}
```

## Fields

| Field | Meaning |
|---|---|
| `id`, `version` | Identity. Results report `id@version`. **Bump `version` on every change** so logs show which configuration produced each result. |
| `categories.<C>.action` | `mask` (replace with placeholder), `keep` (leave as is) or `review` (block, `needs_review`). |
| `categories.<C>.placeholder` | Replacement text. Defaults to `[C]`. |
| `categories.<C>.min_score` | Below this score a detection is *doubtful*. Validated rule hits score 1.0, bad checksums 0.5 and GLiNER its own confidence. |
| `categories.<C>.on_doubt` | What to do with doubtful detections of this category. Falls back to the policy-level `on_doubt`. |
| `categories.<C>.exceptions` | Exact strings to keep, such as the company's public contact email. |
| `priority` | Overlap resolution order: first wins. Every category must appear. |
| `on_doubt` | Default doubt handling. |
| `on_unknown_category` | Action for detector categories the policy does not declare. Default `mask` (fail-safe). |
| `max_chars` | Maximum characters per text field. Larger inputs get `413 too_large` and the gateway blocks them. |
| `reversible` | Policy-wide default (`false`). When `true`, masked values become unique tokens that can be restored. |
| `categories.<C>.reversible` | Per-category override of `reversible`. |

## Reversible tokens

With `reversible`, a masked value is replaced by `[CATEGORY_<10 hex>]` (for example `[DNI_7f3a9c2e41]`) instead of
the fixed placeholder, and the caller keeps a table `token -> original` to rebuild the provider's answer. The same
value always gets the same token, so the model can tell two people apart and refer back to each of them.

The built-in `brain-external-es-reversible@1` is `brain-external-es@3` with `"reversible": true` at policy level and
`"reversible": false` on `IBAN`: the account number is never restorable and still goes out as `[IBAN]`.

- Tokens only appear when the caller asks for them: the gateway on the `chat` route, or `"reversible": true` on
  `POST /v1/mask`. Everywhere else (embeddings, CLI, index masking) the fixed placeholder is used.
- The token is `HMAC-SHA256(key, category + value)`, truncated. It is **not** a plain hash: a DNI has only 10⁸
  possible values, so an unkeyed hash could be reversed by brute force. Without the key a token reveals nothing but
  the category and whether two tokens refer to the same value.
- Values are compared ignoring case and repeated whitespace; the first literal form seen is the one restored.
- A category marked reversible means the **real value returns to whatever consumes the response** (your UI, your
  tools). Keep categories irreversible when the answer should never contain them.

## Choosing doubt handling

| Choice | Effect | When |
|---|---|---|
| `review` | Any doubtful span blocks the whole text | Identifiers where a miss is costly and a doubtful hit is suspicious (bad checksum) |
| `mask` | Doubtful spans are masked; the request goes out | Names and addresses, where over-masking a word is acceptable and blocking long documents is not |
| `keep` | Doubtful spans are left | Only with measured, very low escape rates |

Why `@3` masks doubtful names: on real long documents GLiNER scored true names around 0.47 ("abogada Lucía
Fernández"). With `review`, 25k-character documents were blocked entirely. With `mask`, they go out with no leaks,
at the cost of occasional over-masking of role nouns.

## Sizing `max_chars`

Measure p95 latency on the target server at your expected concurrency and keep
`max_chars × latency_per_char` well under `BRAIN_GATE_TIMEOUT_S`. On an Apple M5 CPU, 25k chars take about 3.5–4.9 s,
close to the 5 s default timeout.

## One policy per brain and destination

The gateway maps `(brain, destination)` to a policy, and each integration token lists the policies it may use.
A second brain is onboarded by adding a policy and a mapping, not by changing code.
