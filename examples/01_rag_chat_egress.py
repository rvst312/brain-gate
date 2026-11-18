"""Caso de uso 1 · Asistente RAG que consulta a un LLM externo sin filtrar datos personales.

Un empleado pregunta al asistente interno por un expediente. El cerebro recupera
fragmentos de documentos (con nombres, DNI, IBAN...), llama a una herramienta y
monta la solicitud al proveedor. BrainGate se interpone en la salida: enmascara
todos los campos, conserva la estructura y solo envía si todo se procesó bien.

    uv run python examples/01_rag_chat_egress.py
"""

import json

from _common import engine

from brain_gate import Gateway, LocalMasker, MockProvider, PolicyStore

# --- 1. Lo que el cerebro ha recuperado de su índice interno (datos ficticios).
retrieved = [
    "Contrato de arrendamiento firmado por María López Sánchez (DNI 12345678Z), "
    "con domicilio en Calle Mayor 12, 3º B, 28013 Madrid.",
    "Domiciliación bancaria: IBAN ES91 2100 0418 4502 0005 1332. Contacto: maria.lopez@example.com, 612 345 678.",
]

# --- 2. La solicitud que se enviaría al LLM: sistema, historial, contexto y herramientas.
request = {
    "model": "cualquier-llm",
    "max_tokens": 600,
    "system": "Eres el asistente interno. Responde solo con el contexto.\n\n" + "\n\n".join(retrieved),
    "messages": [
        {"role": "user", "content": "¿Cuándo vence el contrato de María López y qué cuenta tiene domiciliada?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "buscar_expediente",
             "input": {"cliente": "María López Sánchez", "email": "maria.lopez@example.com"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Expediente 2024/118. Vence el 30/06/2027. Gestor: José Ángel Núñez."},
        ]},
    ],
}

# --- 3. Pasarela: la única ruta autorizada hacia proveedores externos.
policies = PolicyStore.default()
provider = MockProvider()  # en producción: tu cliente de Anthropic, OpenAI, Azure... (ver docs/integration.md)
gateway = Gateway(
    integration_id="assistant-gateway",
    masker=LocalMasker(engine(), policies, allowed_policies={"brain-external-es"}),
    providers={"llm": provider},
    policy_map={("legal-brain", "llm"): "brain-external-es"},
)

result = gateway.send(brain="legal-brain", destination="llm", route="chat", request=request)
print(f"Resultado: {result.status}  (política {result.policy_id})\n")
print("Lo que recibe el proveedor externo:\n")
print(json.dumps(provider.sent[0][1], ensure_ascii=False, indent=2))

# --- 4. Un adjunto sin tratamiento definido se bloquea sin llegar al proveedor.
request["messages"][0]["content"] = [{"type": "image", "source": {"type": "base64", "data": "..."}}]
blocked = gateway.send("legal-brain", "llm", "chat", request)
print(f"\nCon una imagen adjunta: {blocked.status} ({blocked.reason}). Envíos al proveedor: {len(provider.sent)}")
