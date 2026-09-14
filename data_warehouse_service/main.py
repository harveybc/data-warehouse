#!/usr/bin/env python3
"""CLI of the warehouse host: load a configuration, resolve the provider, serve the contract."""

from __future__ import annotations

import argparse
import json
import sys

from .config import load
from .discovery import resolve
from .web import serve


def build(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load_config", "--config", dest="config")
    parser.add_argument("--web_host")
    parser.add_argument("--web_port", type=int)
    parser.add_argument("--store_id")
    parser.add_argument("--print-identity", action="store_true",
                        help="resolve the provider, print what was resolved, and exit")
    args = parser.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items()
                 if v is not None and k not in ("config", "print_identity")}
    config = load(args.config, overrides)
    resolved = resolve(config)
    return {"config": config, "args": args, **resolved}


def main(argv=None) -> int:
    built = build(argv)
    if built["args"].print_identity:
        print(json.dumps(built["identity"], indent=1, sort_keys=True))
        return 0
    return serve(built["config"], built["backend"], built["identity"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
