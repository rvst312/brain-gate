"""Caso de uso 2 · Indexar documentos ya ofuscados para un buscador o RAG.

Antes de trocear y generar embeddings, cada documento se enmascara con la política
de indexación. Al índice general solo llegan texto procesado, una referencia interna
opaca y metadatos limpios (también el título). Los documentos que la política manda
a revisión se apartan para reintentarlos sin reprocesar todo el corpus.

    uv run python examples/02_index_masking.py
"""

import hashlib
import re

from _common import engine

from brain_gate import PolicyStore, Status

DOCS = [
    {"path": "clientes/Lopez_Maria/contrato.txt", "title": "Contrato María López",
     "text": "Arrendataria: María López Sánchez, DNI 12345678Z. Renta mensual: 950 €. "
             "Pago por domiciliación en ES91 2100 0418 4502 0005 1332."},
    {"path": "clientes/Ruiz_Pedro/reclamacion.txt", "title": "Reclamación Pedro Ruiz",
     "text": "D. Pedro Ruiz Gómez reclama la devolución de la fianza. Teléfono de contacto: +34 912 34 56 78."},
    {"path": "plantillas/condiciones.txt", "title": "Condiciones generales",
     "text": "El arrendatario abonará la renta dentro de los cinco primeros días de cada mes."},
    {"path": "clientes/Gil_Ana/alta.txt", "title": "Alta Ana Gil",
     "text": "Documento aportado: 87654321A (revisar, la letra no cuadra)."},  # control inválido
]

eng, policy = engine(), PolicyStore.default().get("brain-external-es")
index: list[dict] = []
pending_review: list[str] = []

for doc in DOCS:
    ref = "doc_" + hashlib.sha256(doc["path"].encode()).hexdigest()[:12]  # referencia interna opaca
    body = eng.mask(doc["text"], policy)
    title = eng.mask(doc["title"], policy)
    if body.status is Status.NEEDS_REVIEW or title.status is Status.NEEDS_REVIEW:
        pending_review.append(ref)
        continue
    # Trocear el texto ya procesado; los embeddings se generarían a partir de estos mismos fragmentos.
    chunks = [c for c in re.split(r"(?<=[.!?])\s+", body.masked_text) if c]
    for i, chunk in enumerate(chunks):
        index.append({"id": f"{ref}#{i}", "doc_ref": ref, "title": title.masked_text, "text": chunk,
                      "policy": body.policy_version, "detector": body.detector_version})

print(f"Fragmentos indexados: {len(index)}  ·  Documentos en revisión: {pending_review}\n")
for row in index:
    print(f"{row['id']:<20} {row['title']:<28} {row['text']}")


def search(q: str) -> list[str]:
    return sorted({r["doc_ref"] for r in index if q.lower() in r["text"].lower()})


print("\nBuscar 'fianza'    ->", search("fianza"))
print("Buscar 'López'     ->", search("López"), "(el nombre ya no está en el índice general)")
print("Buscar '12345678Z' ->", search("12345678Z"))
print("\nPara localizar por identificador exacto, usa una búsqueda local restringida sobre los originales,"
      "\naplicando los permisos del documento (ver docs/architecture.md).")
