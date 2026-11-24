"""Caso de uso 4 · Reconstrucción del dato: el modelo trabaja con tokens y tú recibes los valores reales.

Con una política reversible cada dato sale como un token único (``[DNI_7f3a9c2e41]``)
en lugar de un placeholder fijo: el modelo distingue a dos personas distintas y puede
citarlas. La pasarela guarda la tabla de equivalencias en memoria durante la petición
y reconstruye la respuesta, incluidos los argumentos de las llamadas a herramientas.

    uv run python examples/04_reversible_chat.py
"""

import json

from _common import engine

from brain_gate import Gateway, LocalMasker, MockProvider, PolicyStore

POLICY = "brain-external-es-reversible"

request = {
    "model": "cualquier-llm",
    "max_tokens": 600,
    "system": "Eres el asistente interno. Contrato firmado por el titular del DNI 12345678Z. "
              "Contacto: maria.lopez@example.com, 612 345 678. Domiciliación: IBAN ES91 2100 0418 4502 0005 1332.",
    "messages": [
        {"role": "user", "content": "Envía un recordatorio al correo maria.lopez@example.com citando su DNI."},
    ],
}


def fake_llm(route, payload):
    """Simula al modelo: solo ve tokens y los reutiliza en el texto y en una llamada a herramienta."""
    system = payload["system"]
    dni = system.split("DNI ")[1].split(".")[0]
    email = payload["messages"][0]["content"].split("correo ")[1].split(" ")[0]
    return {"role": "assistant", "content": [
        {"type": "text", "text": f"Envío el recordatorio a {email} indicando el DNI {dni}."},
        {"type": "tool_use", "id": "t1", "name": "enviar_correo", "input": {"to": email, "asunto": f"Contrato {dni}"}},
    ]}


policies = PolicyStore.default()
provider = MockProvider(reply=fake_llm)
gateway = Gateway(
    integration_id="assistant-gateway",
    masker=LocalMasker(engine(), policies, allowed_policies={POLICY}),
    providers={"llm": provider},
    policy_map={("legal-brain", "llm"): POLICY},
    # token_key=b"...": fija la clave si quieres los mismos tokens entre reinicios o procesos.
)

result = gateway.send(brain="legal-brain", destination="llm", route="chat", request=request)
print(f"Resultado: {result.status}  (política {result.policy_id})\n")
print("Lo que recibe el proveedor externo:\n")
print(json.dumps(provider.sent[0][1], ensure_ascii=False, indent=2))
print(f"\nRespuesta reconstruida ({result.restored} tokens restaurados):\n")
print(json.dumps(result.response, ensure_ascii=False, indent=2))
