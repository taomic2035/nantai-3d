"""Controlled child behaviors for Studio ProcessController tests."""

from __future__ import annotations

import os
import sys


def main() -> int:
    mode = sys.argv[1]
    if mode == "success":
        print("hello from stdout", flush=True)
        print("hello from stderr", file=sys.stderr, flush=True)
        return 0
    if mode == "failure":
        print("controlled failure", file=sys.stderr, flush=True)
        return 7
    if mode == "flood":
        for index in range(2_000):
            print(f"out-{index:04d}-" + "x" * 128)
            print(f"err-{index:04d}-" + "y" * 128, file=sys.stderr)
        return 0
    if mode == "invalid-utf8":
        os.write(sys.stdout.fileno(), b"before-\xff-after\n")
        return 0
    if mode == "long-secret":
        secret = sys.argv[2]
        print("prefix-" + secret + "-" + "z" * 20_000, flush=True)
        return 0
    if mode == "split-secret":
        secret = sys.argv[2]
        prefix = "p" * (64 * 1024 - len(secret) // 2)
        print(prefix + secret + "-tail", flush=True)
        return 0
    raise ValueError(mode)


if __name__ == "__main__":
    raise SystemExit(main())
