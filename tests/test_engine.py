from brain_gate.contextual import GlinerDetector, make_windows
from brain_gate.engine import Engine, Status, merge_same_category, resolve_overlaps
from brain_gate.policy import Action, CategoryRule
from brain_gate.rules import RuleDetector
from brain_gate.spans import Span

from conftest import LABELS, FakeGliner


def test_contract_example(engine, policy):
    r = engine.mask("María López pidió que le escribieran a maria@example.com", policy)
    assert r.status is Status.MASKED
    assert r.masked_text == "[PERSONA] pidió que le escribieran a [EMAIL]"
    assert r.policy_version == "brain-external-es@3"
    assert r.detector_version == "rules@1+gliner-pii@1"


def test_full_name_single_span(engine, policy):
    r = engine.mask("Firmado: José Ángel Núñez.", policy)
    assert r.masked_text == "Firmado: [PERSONA]."


def test_text_integrity_outside_substitutions(engine, policy):
    text = "¡Hola! 👋🏽\nMaría López — tlf 612 345 678\n\tcañón, pingüino, 日本語 ✅\r\nfin"
    r = engine.mask(text, policy)
    assert r.masked_text == "¡Hola! 👋🏽\n[PERSONA] — tlf [TELEFONO]\n\tcañón, pingüino, 日本語 ✅\r\nfin"


def test_offsets_are_codepoints(engine, policy):
    text = "👋🏽👋🏽 María López"
    r = engine.mask(text, policy)
    (d,) = r.decisions
    assert text[d.span.start:d.span.end] == "María López"
    assert d.span.start == 5  # 4 puntos de código de emoji + espacio


def test_no_detections_returns_input(engine, policy):
    text = "Reunión el martes a las 10:00 en la sala 3."
    r = engine.mask(text, policy)
    assert r.status is Status.MASKED and r.masked_text == text and r.decisions == []


def test_empty_text(engine, policy):
    r = engine.mask("", policy)
    assert r.status is Status.MASKED and r.masked_text == ""


def test_doubtful_detection_needs_review(engine, policy):
    r = engine.mask("DNI 12345678A de María López", policy)
    assert r.status is Status.NEEDS_REVIEW
    assert r.masked_text is None


def test_doubtful_name_is_masked_not_reviewed(policy):
    # PERSONA y DIRECCION enmascaran las dudas; los identificadores siguen yendo a revisión.
    model = FakeGliner({"Lucía Fernández": "person"})
    model_low = type("M", (), {"predict_entities": lambda self, t, l, threshold=0.4: [
        dict(e, score=0.47) for e in model.predict_entities(t, l)]})()
    r = Engine([RuleDetector(), GlinerDetector(model_low, LABELS)]).mask("la abogada Lucía Fernández", policy)
    assert r.status is Status.MASKED and r.masked_text == "la abogada [PERSONA]"


def test_exception_kept(engine, policy):
    p = policy.model_copy(deep=True)
    p.categories["EMAIL"] = CategoryRule(action=Action.MASK, placeholder="[EMAIL]", exceptions=["info@empresa.example"])
    r = engine.mask("info@empresa.example y maria@example.com", p)
    assert r.masked_text == "info@empresa.example y [EMAIL]"


def test_duplicates_merge():
    spans = [Span(0, 5, "PERSONA", 0.7, "a"), Span(3, 9, "PERSONA", 0.9, "b"), Span(9, 12, "PERSONA")]
    (m,) = merge_same_category(spans)
    assert (m.start, m.end, m.score) == (0, 12, 1.0)


def test_overlap_priority_and_union(policy):
    # GLiNER marca como DIRECCION un tramo que contiene un correo: gana EMAIL por
    # prioridad, pero se extiende a la unión para no dejar escapar el resto.
    spans = [Span(10, 30, "DIRECCION", 0.8, "gliner"), Span(5, 20, "EMAIL")]
    (w,) = resolve_overlaps(spans, policy)
    assert (w.start, w.end, w.category) == (5, 30, "EMAIL")


def test_windows_cover_everything_and_overlap():
    text = ("Frase número %d con algo de contenido. " * 200) % tuple(range(200))
    ws = make_windows(text, size=300, overlap=60)
    assert ws[0].start == 0 and ws[-1].start + len(ws[-1].text) == len(text)
    for a, b in zip(ws, ws[1:]):
        assert b.start < a.start + len(a.text), "las ventanas deben solaparse"
        assert text[a.start:a.start + len(a.text)] == a.text


def test_entity_crossing_window_boundary(policy):
    filler = "palabra " * 40
    text = filler + "José Ángel Núñez" + " " + filler
    model = FakeGliner({"José Ángel Núñez": "person"})
    det = GlinerDetector(model, LABELS, window_size=330, window_overlap=60)
    assert len(make_windows(text, 330, 60)) > 1
    r = Engine([RuleDetector(), det]).mask(text, policy)
    assert "José" not in r.masked_text and "Núñez" not in r.masked_text
    assert r.masked_text.count("[PERSONA]") == 1


def test_long_text_offsets_recomposed(policy):
    text = ("Texto de relleno sin datos. " * 100) + "Contactar con María López en maria@example.com."
    model = FakeGliner({"María López": "person"})
    r = Engine([RuleDetector(), GlinerDetector(model, LABELS, window_size=400, window_overlap=80)]).mask(text, policy)
    assert r.masked_text.endswith("Contactar con [PERSONA] en [EMAIL].")
    assert len(model.calls) > 1
