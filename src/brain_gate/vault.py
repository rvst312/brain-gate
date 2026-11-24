"""Tabla de equivalencias para reconstruir el dato.

Cada valor enmascarado de una categoría reversible se sustituye por un token
único ``[CATEGORIA_<hex>]`` y la tabla guarda ``token -> original``. El token es
un HMAC con clave del valor normalizado: el mismo dato produce siempre el mismo
token con la misma clave, y sin la clave no se puede revertir por fuerza bruta
(un DNI solo tiene 10⁸ combinaciones; un hash sin clave no protegería nada).

La tabla vive en memoria y dura lo que dura quien la crea (una petición del
gateway). Nunca se escribe en disco ni en telemetría.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any

TOKEN_HEX = 10
_BRACKETED = re.compile(r"\[([^\[\]\s]+_[0-9a-f]+)\]")


def _normalize(value: str) -> str:
    """Identidad del dato: sin diferencias de espacios ni de mayúsculas."""
    return " ".join(value.split()).casefold()


class Vault:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)
        self._by_token: dict[str, str] = {}             # "DNI_7f3a9c2e41" -> original
        self._by_value: dict[tuple[str, str], str] = {}  # (categoría, normalizado) -> token
        self._pattern: re.Pattern[str] | None = None

    def __len__(self) -> int:
        return len(self._by_token)

    def __repr__(self) -> str:  # nunca mostrar contenido
        return f"Vault(entries={len(self)})"

    def _digest(self, category: str, norm: str) -> str:
        return hmac.new(self._key, f"{category}\0{norm}".encode(), hashlib.sha256).hexdigest()

    def token(self, category: str, value: str) -> str:
        """Registra el valor y devuelve su token. La primera forma literal vista es la que se restaura."""
        norm = _normalize(value)
        bare = self._by_value.get((category, norm))
        if bare is None:
            digest = self._digest(category, norm)
            n = TOKEN_HEX
            bare = f"{category}_{digest[:n]}"
            # Colisión con otro valor: se alarga el token hasta que sea único.
            while bare in self._by_token and _normalize(self._by_token[bare]) != norm and n < len(digest):
                n += 2
                bare = f"{category}_{digest[:n]}"
            self._by_value[(category, norm)] = bare
            self._by_token.setdefault(bare, value)
            self._pattern = None
        return f"[{bare}]"

    def mappings(self) -> dict[str, str]:
        return {f"[{bare}]": original for bare, original in self._by_token.items()}

    def merge(self, mappings: dict[str, str]) -> None:
        """Incorpora una tabla ``token -> original`` generada en otro proceso (servicio HTTP).

        Un token ya conocido con un valor distinto es una colisión entre tablas
        independientes: se rechaza antes que restaurar un dato equivocado.
        """
        staged: dict[str, str] = {}
        for token, original in mappings.items():
            m = _BRACKETED.fullmatch(token) if isinstance(token, str) else None
            if m is None or not isinstance(original, str) or not original:
                raise ValueError("tabla de equivalencias no válida")
            bare = m.group(1)
            known = self._by_token.get(bare)
            if known is not None and _normalize(known) != _normalize(original):
                raise ValueError("colisión de tokens")
            staged[bare] = original
        for bare, original in staged.items():
            if bare not in self._by_token:
                self._by_token[bare] = original
                self._by_value.setdefault((bare.rsplit("_", 1)[0], _normalize(original)), bare)
                self._pattern = None

    def _compiled(self) -> re.Pattern[str]:
        if self._pattern is None:
            alt = "|".join(re.escape(b) for b in sorted(self._by_token, key=len, reverse=True))
            # Con corchetes, o desnudo si el modelo los ha quitado (sin pegarse a otra palabra).
            self._pattern = re.compile(rf"\[({alt})\]|(?<![0-9A-Za-z_])({alt})(?![0-9A-Za-z_])")
        return self._pattern

    def restore(self, text: str) -> tuple[str, int]:
        """Sustituye los tokens conocidos por su valor original. Los desconocidos no se tocan."""
        if not self._by_token:
            return text, 0
        return self._compiled().subn(lambda m: self._by_token[m.group(1) or m.group(2)], text)

    def restore_obj(self, obj: Any) -> tuple[Any, int]:
        """Copia de ``obj`` con todas las hojas ``str`` de dicts y listas restauradas."""
        if isinstance(obj, str):
            return self.restore(obj)
        if isinstance(obj, dict):
            total, out_d = 0, {}
            for k, v in obj.items():
                out_d[k], n = self.restore_obj(v)
                total += n
            return out_d, total
        if isinstance(obj, list):
            total, out_l = 0, []
            for v in obj:
                r, n = self.restore_obj(v)
                out_l.append(r)
                total += n
            return out_l, total
        return obj, 0
