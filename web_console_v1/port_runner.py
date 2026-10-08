"""Launch a local Python web server with an in-memory port override.

This tool never modifies source files. It rewrites Python NUMBER tokens equal
to the old port in memory, and sets conventional port env vars, then runs the
script using its original path as __file__.
"""
import io
import os
import sys
import token
import tokenize
from pathlib import Path


def replace_port_literals(source: str, old_port: int, new_port: int) -> tuple[str, int]:
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    replaced = 0
    new_tokens = []
    for t in tokens:
        if t.type == token.NUMBER and t.string == str(old_port):
            t = t._replace(string=str(new_port))
            replaced += 1
        new_tokens.append(t)
    return tokenize.untokenize(new_tokens), replaced


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: port_runner.py SCRIPT OLD_PORT NEW_PORT", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    old_port = int(sys.argv[2])
    new_port = int(sys.argv[3])
    if not path.is_file() or path.suffix.lower() != ".py":
        print("Only existing .py files can use temporary port remapping.", file=sys.stderr)
        return 2
    if old_port == new_port or not all(1 <= p <= 65535 for p in (old_port, new_port)):
        print("Invalid port override.", file=sys.stderr)
        return 2

    with tokenize.open(path) as f:
        source = f.read()
    modified, count = replace_port_literals(source, old_port, new_port)
    print(
        f"PDE port conflict: using temporary localhost port {new_port} instead of {old_port}; "
        f"updated {count} numeric literal(s) in memory; no project files changed.",
        flush=True,
    )
    for name in ("PORT", "APP_PORT", "SERVER_PORT", "WORKBENCH_PORT", "PDE_WORKBENCH_PORT"):
        os.environ[name] = str(new_port)

    # Preserve the original script path for imports and relative resource lookups.
    sys.path.insert(0, str(path.parent))
    sys.argv = [str(path)]
    globals_for_script = {
        "__name__": "__main__",
        "__file__": str(path),
        "__package__": None,
        "__cached__": None,
        "__spec__": None,
    }
    exec(compile(modified, str(path), "exec"), globals_for_script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
