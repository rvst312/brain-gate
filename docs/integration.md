# Integrating BrainGate with your brain

## 1. Make the gateway the only way out

List every call that can leave the brain (chat completions, embeddings, tool or agent frameworks, direct SDK
calls in scripts) and route them all through `Gateway.send`. A call that bypasses the gateway is unprotected.

## 2. Write a provider

A provider is any object with `send(route, payload) -> dict`. It receives the **already masked** payload.
Example with an HTTP API (adapt the endpoint, headers and payload mapping to your vendor):

```python
import httpx
from brain_gate import Gateway, LocalMasker, PolicyStore, build_engine

class HttpLLMProvider:
    def __init__(self, url: str, api_key: str):
        self.client = httpx.Client(timeout=60)
        self.url, self.key = url, api_key

    def send(self, route: str, payload: dict) -> dict:
        r = self.client.post(f"{self.url}/{route}", json=payload,
                             headers={"Authorization": f"Bearer {self.key}"})
        r.raise_for_status()
        return r.json()

engine = build_engine("models/gliner_multi_pii-v1")
gateway = Gateway(
    integration_id="assistant-gateway",
    masker=LocalMasker(engine, PolicyStore.default(), allowed_policies={"brain-external-es"}),
    providers={"llm": HttpLLMProvider("https://llm.example", api_key="...")},
    policy_map={("my-brain", "llm"): "brain-external-es"},
)

result = gateway.send("my-brain", "llm", "chat", request)
if result.status == "blocked":
    # Controlled state: tell the user the request couldn't be sent, log result.reason (no content).
    ...
```

The gateway's internal chat format is provider-neutral (`system`, `messages`, parts `text` / `tool_use` /
`tool_result`). Translate to your vendor's exact schema **inside** the provider, after masking. Fields the walker
doesn't know are blocked rather than forwarded unmasked, so add new fields to `gateway.py` explicitly.

## 3. Local or remote masker

| Masker | Use when |
|---|---|
| `LocalMasker(engine, policies, allowed)` | The gateway runs in Python and can hold the model (~2.4 GB RAM) |
| `HttpMasker(base_url, token)` | Several services or languages share one masking service (`braingate serve`) |

Both return `masked`, `needs_review` or `error`, and the gateway blocks on anything but `masked`.
`HttpMasker` treats any unexpected HTTP status or body as an error.

## 4. Test against a mock first

```python
from brain_gate import MockProvider
provider = MockProvider()
# ... build the gateway with {"llm": provider}, replay real traffic shapes ...
assert "12345678Z" not in str(provider.sent)
```

`tests/test_gateway.py` is a template: PII injected through history, documents and tools, plus detector down,
timeout, review and unsupported fields, each asserting that `provider.sent` stays empty.

## 5. Index-time masking

Mask before chunking and before computing embeddings, store an opaque document reference, and mask titles and
paths as well. See `examples/02_index_masking.py`. Re-process by document version so retries replace chunks
instead of duplicating them, and keep `needs_review` documents in a separate queue.
