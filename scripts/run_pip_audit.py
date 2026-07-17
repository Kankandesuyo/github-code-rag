"""Run pip-audit with a single, automatically expiring security waiver."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from datetime import date


ALLOWED_WAIVERS = frozenset({"PYSEC-2026-311"})
WAIVER_EXPIRES_ON = date(2026, 8, 13)


def build_audit_command(*, today: date) -> list[str]:
    command = [sys.executable, "-m", "pip_audit", "-r", "requirements.txt"]
    if today < WAIVER_EXPIRES_ON:
        for waiver in sorted(ALLOWED_WAIVERS):
            command.extend(["--ignore-vuln", waiver])
    return command


def run_audit(
    *,
    today: date | None = None,
    runner: Callable[[list[str]], int] = subprocess.call,
) -> int:
    return runner(build_audit_command(today=today or date.today()))


if __name__ == "__main__":
    raise SystemExit(run_audit())
