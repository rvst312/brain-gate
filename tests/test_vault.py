"""Tabla de equivalencias: tokens únicos y reconstrucción del dato."""

import re

import pytest

from brain_gate.vault import Vault

KEY = b"clave-de-prueba"
TOKEN_RE = re.compile(r"\[DNI_[0-9a-f]{10}\]")


def test_token_format_and_determinism():
    a, b = Vault(KEY), Vault(KEY)
    t = a.token("DNI", "12345678Z")
    assert TOKEN_RE.fullmatch(t)
    assert t == a.token("DNI", "12345678Z") == b.token("DNI", "12345678Z")
    assert len(a) == 1


def test_token_depends_on_key_category_and_value():
    v = Vault(KEY)
    t = v.token("DNI", "12345678Z")
    assert t != Vault(b"otra-clave").token("DNI", "12345678Z")
    assert t != v.token("DNI", "87654321X")
    assert t.split("_")[1] != v.token("NIE", "12345678Z").split("_")[1]
    assert Vault().token("DNI", "12345678Z") != Vault().token("DNI", "12345678Z")  # clave aleatoria


def test_normalization_keeps_first_surface_form():
    v = Vault(KEY)
    t = v.token("PERSONA", "María López")
    assert v.token("PERSONA", "maría   lópez") == t
    assert v.restore(t) == ("María López", 1)


def test_restore_with_and_without_brackets():
    v = Vault(KEY)
    t = v.token("DNI", "12345678Z")
    bare = t[1:-1]
    text, n = v.restore(f"El DNI {t} y otra vez {bare}. Lista: [{bare}, x]")
    assert text == "El DNI 12345678Z y otra vez 12345678Z. Lista: [12345678Z, x]"
    assert n == 3


def test_unknown_or_altered_tokens_are_left_alone():
    v = Vault(KEY)
    t = v.token("DNI", "12345678Z")
    bare = t[1:-1]
    for text in ("[DNI_0000000000]", "[DNI]", f"x{bare}", f"{bare}ff", bare[:-1]):
        assert v.restore(text) == (text, 0)
    assert Vault(KEY).restore(t) == (t, 0)  # tabla vacía


def test_restored_value_is_not_rescanned():
    v = Vault(KEY)
    inner = v.token("DNI", "12345678Z")
    outer = v.token("PERSONA", f"Sr. {inner}")
    assert v.restore(outer) == (f"Sr. {inner}", 1)


def test_collision_extends_token(monkeypatch):
    v = Vault(KEY)
    monkeypatch.setattr(v, "_digest", lambda category, norm: "a" * 10 + ("b" if norm == "uno" else "c") * 54)
    t1, t2 = v.token("X", "uno"), v.token("X", "dos")
    assert t1 == "[X_aaaaaaaaaa]" and t2 == "[X_aaaaaaaaaacc]"
    assert v.restore(f"{t1} {t2}") == ("uno dos", 2)


def test_restore_obj_walks_structure_without_mutating():
    v = Vault(KEY)
    t = v.token("EMAIL", "maria@example.com")
    obj = {"content": [{"type": "text", "text": f"Escribe a {t}"},
                       {"type": "tool_use", "input": {"to": t, "n": 3, "ok": True, "x": None}}], t: "clave"}
    out, n = v.restore_obj(obj)
    assert n == 2
    assert out["content"][0]["text"] == "Escribe a maria@example.com"
    assert out["content"][1]["input"] == {"to": "maria@example.com", "n": 3, "ok": True, "x": None}
    assert t in out and obj["content"][0]["text"] == f"Escribe a {t}"  # claves y original intactos


def test_merge_roundtrip_and_conflict():
    src = Vault(KEY)
    t = src.token("PERSONA", "María López")
    dst = Vault(KEY)
    dst.merge(src.mappings())
    dst.merge({t: "maría lópez"})  # mismo dato con otra forma: se conserva la primera
    assert dst.restore(t) == ("María López", 1)
    assert dst.token("PERSONA", "María López") == t
    with pytest.raises(ValueError):
        dst.merge({t: "Otra Persona"})


@pytest.mark.parametrize("bad", [{"DNI_abc": "x"}, {"[DNI]": "x"}, {"[DNI_ab12]": 5}, {"[DNI_ab12]": ""}, {5: "x"}])
def test_merge_rejects_malformed(bad):
    v = Vault(KEY)
    with pytest.raises(ValueError):
        v.merge(bad)
    assert len(v) == 0


def test_repr_has_no_content():
    v = Vault(KEY)
    v.token("DNI", "12345678Z")
    assert "12345678Z" not in repr(v) and "DNI_" not in repr(v)
