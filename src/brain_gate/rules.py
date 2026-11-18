"""Detector local por reglas: correo, teléfono, DNI, NIE e IBAN.

Donde existe dígito de control se valida. Un candidato con la forma correcta pero
control inválido se devuelve con puntuación baja para que la política decida
(tratamiento de dudas) en lugar de descartarlo en silencio.
"""

from __future__ import annotations

import re

from .spans import Span

DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
LOW_CONFIDENCE = 0.5

_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])",
    re.UNICODE,
)
# Teléfonos españoles (fijo y móvil) con prefijo opcional, y números
# internacionales con prefijo explícito.
_PHONE = re.compile(
    r"(?<![\w+])"
    r"(?:(?:\+|00)34[\s.-]?)?[6789](?:[\s.-]?\d){8}(?![\w])"
    r"|(?<![\w+])\+(?!34)\d{1,3}[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4}){2,4}(?!\w)"
)
_DNI = re.compile(r"(?<![\w-])(\d{8})[\s-]?([A-Za-z])(?!\w)")
_NIE = re.compile(r"(?<![\w-])([XYZxyz])[\s-]?(\d{7})[\s-]?([A-Za-z])(?!\w)")
_IBAN = re.compile(r"(?<!\w)([A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,7}(?:[ ]?[A-Za-z0-9]{1,3})?)(?!\w)")

_IBAN_LENGTHS = {"ES": 24, "PT": 25, "FR": 27, "DE": 22, "IT": 27, "GB": 22, "NL": 18, "BE": 16, "AD": 24}


def dni_valid(number: str, letter: str) -> bool:
    return DNI_LETTERS[int(number) % 23] == letter.upper()


def nie_valid(prefix: str, number: str, letter: str) -> bool:
    return dni_valid(str("XYZ".index(prefix.upper())) + number, letter)


def iban_valid(raw: str) -> bool:
    iban = raw.replace(" ", "").upper()
    expected = _IBAN_LENGTHS.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False
    if not 15 <= len(iban) <= 34:
        return False
    digits = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def _phone_digits_ok(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    return 9 <= len(digits) <= 15


class RuleDetector:
    name = "rules"
    version = "rules@1"

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for m in _EMAIL.finditer(text):
            spans.append(Span(m.start(), m.end(), "EMAIL"))
        for m in _IBAN.finditer(text):
            if not re.search(r"\d", m.group(1)[4:]):
                continue
            score = 1.0 if iban_valid(m.group(1)) else LOW_CONFIDENCE
            spans.append(Span(m.start(1), m.end(1), "IBAN", score))
        for m in _NIE.finditer(text):
            score = 1.0 if nie_valid(*m.groups()) else LOW_CONFIDENCE
            spans.append(Span(m.start(), m.end(), "NIE", score))
        for m in _DNI.finditer(text):
            score = 1.0 if dni_valid(*m.groups()) else LOW_CONFIDENCE
            spans.append(Span(m.start(), m.end(), "DNI", score))
        for m in _PHONE.finditer(text):
            if _phone_digits_ok(m.group(0)):
                spans.append(Span(m.start(), m.end(), "TELEFONO", 0.95))
        return spans
