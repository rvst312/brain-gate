import pytest

from brain_gate.rules import RuleDetector, dni_valid, iban_valid, nie_valid


def cats(text):
    return {(text[s.start:s.end], s.category, s.score == 1.0 or s.score >= 0.9) for s in RuleDetector().detect(text)}


def test_validators():
    assert dni_valid("12345678", "Z")
    assert not dni_valid("12345678", "A")
    assert nie_valid("X", "1234567", "L")
    assert iban_valid("ES91 2100 0418 4502 0005 1332")
    assert not iban_valid("ES91 2100 0418 4502 0005 1333")


@pytest.mark.parametrize("text,value,category", [
    ("escribe a maria.lopez+info@example.com ya", "maria.lopez+info@example.com", "EMAIL"),
    ("DNI 12345678Z del titular", "12345678Z", "DNI"),
    ("DNI 12345678-Z del titular", "12345678-Z", "DNI"),
    ("NIE X1234567L", "X1234567L", "NIE"),
    ("cuenta ES91 2100 0418 4502 0005 1332.", "ES91 2100 0418 4502 0005 1332", "IBAN"),
    ("cuenta ES9121000418450200051332.", "ES9121000418450200051332", "IBAN"),
    ("llamar al 612 345 678 mañana", "612 345 678", "TELEFONO"),
    ("tel. +34 912 34 56 78", "+34 912 34 56 78", "TELEFONO"),
])
def test_detects(text, value, category):
    assert (value, category, True) in cats(text)


def test_bad_checksum_is_low_confidence():
    found = cats("DNI 12345678A")
    assert ("12345678A", "DNI", False) in found


def test_no_false_positive_on_plain_text():
    assert RuleDetector().detect("Expediente 2024/118, importe 1.250,00 €, plazo de 15 días.") == []
