<div align="center">

# 🧠🛡️ BrainGate

**Escudo local de datos personales para asistentes de IA. Los datos sensibles nunca salen de tu servidor sin enmascarar.**

[English](README.md) · [Arquitectura](docs/architecture.md) · [Políticas](docs/policies.md) · [API](docs/api.md) · [Integración](docs/integration.md)

</div>

---

Tu cerebro empresarial (asistente RAG, copiloto interno, base de conocimiento) está lleno de nombres, DNI,
IBAN, correos y direcciones. Cada vez que llama a un LLM o a una API de embeddings externa, esos datos viajan con la solicitud.

**BrainGate se interpone entre tu cerebro y el exterior.** Detecta datos personales con reglas locales y un
modelo NER local, los enmascara según una política versionada y solo deja salir la solicitud cuando todos los
campos están procesados. Si hay dudas que la política manda revisar, un fallo o un formato sin soporte, **bloquea** la salida.

```text
"María López pidió que le escribieran a maria@example.com"
                              │
                         BrainGate (local)
                              ▼
"[PERSONA] pidió que le escribieran a [EMAIL]"   ──►  LLM externo
```

## Qué aporta

- **Inferencia 100 % local.** Los pesos se cargan desde disco con la red desactivada.
- **Pensado para documentos en español.** DNI, NIE e IBAN con dígito de control, teléfonos españoles y correos. Nombres y direcciones con [GLiNER Multi PII](https://huggingface.co/urchade/gliner_multi_pii-v1).
- **Pasarela que falla cerrada.** Enmascara sistema, historial, contexto recuperado, llamadas y resultados de herramientas conservando la estructura. Si hay revisión pendiente, el detector cae, se agota el tiempo o llega un adjunto o un campo desconocido, la solicitud se bloquea.
- **Políticas versionadas.** Categorías, marcadores, umbrales, excepciones, prioridad y tratamiento de dudas son configuración. Cada resultado registra `policy_version` y `detector_version`.
- **Logs sin contenido.** Solo latencias, tamaños, recuentos y versiones. Los errores nunca devuelven el texto.
- **Tres formas de uso.** Biblioteca Python, API privada (`POST /v1/mask`) o CLI.

## Empezar

```bash
git clone https://github.com/rvst312/brain-gate && cd brain-gate
uv sync --extra gliner --extra server
uv run braingate download-model            # ~1,16 GB, revisiones fijadas (único paso con red)
echo "Firmado: José Ángel Núñez García, DNI 12345678Z, Calle Mayor 12, 3º B, Madrid" | uv run braingate mask
# Firmado: [PERSONA], DNI [DNI], [DIRECCION]
```

Sin modelo: `uv run braingate mask --gliner none "..."` usa solo reglas.

> **macOS:** si aparece `No module named 'brain_gate'`, macOS ha marcado como oculto el `.pth` de la instalación
> editable y Python lo ignora: `chflags nohidden .venv/lib/python3.12/site-packages/*.pth` o usa `PYTHONPATH=src`.

## Casos de uso

| Ejemplo | Qué muestra |
|---|---|
| [`01_rag_chat_egress.py`](examples/01_rag_chat_egress.py) | Un asistente RAG llama a un LLM externo. Se enmascaran contexto, historial y herramientas. Con un adjunto, la solicitud se bloquea sin llamar al proveedor. |
| [`02_index_masking.py`](examples/02_index_masking.py) | Indexación ofuscada: texto y títulos enmascarados, referencia opaca, versiones registradas y cola de revisión para un DNI con letra incorrecta. |
| [`03_http_service.sh`](examples/03_http_service.sh) | Servicio HTTP privado con token por integración y permiso por política. |

```bash
uv run python examples/01_rag_chat_egress.py
```

## Cómo funciona

1. **Detectar.** Reglas con validación de dígitos de control y GLiNER sobre ventanas solapadas cortadas en fin de frase.
2. **Resolver.** Se unen los duplicados de la misma categoría, también los partidos entre ventanas. Entre categorías gana la de mayor prioridad, extendida a la unión para que no escape ningún resto.
3. **Decidir.** La política asigna `mask`, `keep` (excepciones) o `review`. Las detecciones dudosas siguen el `on_doubt` de su categoría.
4. **Aplicar.** Se sustituye sobre el original y se conserva todo lo demás (tildes, emojis, saltos de línea). Las posiciones son puntos de código Unicode.

Detalle completo en [docs/architecture.md](docs/architecture.md).

## Pruebas

```bash
uv run pytest
```

Cubren detección en español, validadores, solapamientos, entidades que cruzan ventanas, integridad del texto,
autenticación y permisos, límites, timeouts, caída del detector, ausencia de datos en logs y errores, y un
proveedor simulado que demuestra que todo modo de fallo bloquea la llamada. Las pruebas con el modelo real se
ejecutan si los pesos están en `models/` y comprueban que la carga no abre ninguna conexión de red.

## Limitaciones

- Solo texto. Los adjuntos se bloquean.
- La política de ejemplo enmascara nombres y direcciones dudosos: a veces se enmascara una palabra que no es un dato personal («El arrendatario»).
- BrainGate aplica la política que definas. La adecuación al RGPD u otra normativa corresponde a quien define y aprueba la política.

## Licencia

[Apache 2.0](LICENSE).
