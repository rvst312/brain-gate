"""Línea de comandos: ``braingate mask | serve | download-model``."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .models import DEFAULT_MODEL_DIR


def _gliner_path(arg: str | None) -> str | None:
    if arg == "none":
        return None
    path = arg or os.environ.get("BRAIN_GATE_GLINER_PATH")
    if path:
        return path
    return str(DEFAULT_MODEL_DIR) if (DEFAULT_MODEL_DIR / "pytorch_model.bin").is_file() else None


def cmd_mask(args: argparse.Namespace) -> int:
    from .api import build_engine
    from .engine import Status
    from .policy import PolicyStore

    store = PolicyStore.from_dir(args.policy_dir) if args.policy_dir else PolicyStore.default()
    policy = store.get(args.policy)
    if policy is None:
        print(f"política desconocida: {args.policy} (disponibles: {', '.join(store.ids())})", file=sys.stderr)
        return 2
    gliner = _gliner_path(args.gliner)
    if gliner is None:
        print("aviso: GLiNER no disponible, solo reglas (nombres y direcciones no se detectan). "
              "Ejecuta `braingate download-model`.", file=sys.stderr)
    engine = build_engine(gliner)
    text = args.text if args.text is not None else sys.stdin.read()
    if len(text) > policy.max_chars:
        print(json.dumps({"status": "error", "error": "too_large"}))
        return 1
    r = engine.mask(text, policy)
    if args.json or args.spans:
        out = {"status": r.status.value, "policy_version": r.policy_version, "detector_version": r.detector_version}
        if r.status is Status.MASKED:
            out["masked_text"] = r.masked_text
        if args.spans:
            out["spans"] = [{"start": d.span.start, "end": d.span.end, "category": d.span.category,
                             "action": d.action.value} for d in r.decisions]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif r.status is Status.MASKED:
        sys.stdout.write(r.masked_text)
        if not r.masked_text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print("needs_review: la política exige revisión; no se devuelve texto.", file=sys.stderr)
        return 3
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    gliner = _gliner_path(args.gliner)
    if gliner:
        os.environ["BRAIN_GATE_GLINER_PATH"] = gliner
    uvicorn.run("brain_gate.api:app_from_env", factory=True, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    from .models import download_gliner

    print(f"Descargando GLiNER Multi PII v1 (~1,16 GB) en {args.dest} ...", file=sys.stderr)
    print(download_gliner(args.dest))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="braingate", description="Ofuscador local de datos sensibles para IA.")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mask", help="enmascara texto (argumento o stdin)")
    m.add_argument("text", nargs="?")
    m.add_argument("--policy", default="brain-external-es")
    m.add_argument("--policy-dir")
    m.add_argument("--gliner", help="ruta a los pesos; 'none' para solo reglas")
    m.add_argument("--json", action="store_true")
    m.add_argument("--spans", action="store_true", help="incluye categorías y posiciones (sin texto)")
    m.set_defaults(func=cmd_mask)

    s = sub.add_parser("serve", help="arranca la API privada")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8088)
    s.add_argument("--gliner")
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("download-model", help="descarga los pesos fijados de GLiNER")
    d.add_argument("--dest", default=str(DEFAULT_MODEL_DIR))
    d.set_defaults(func=cmd_download)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
