"""Fail-fast local environment validation without exposing secrets."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = (3, 10)
REQUIRED_PACKAGES = {
    "argon2-cffi": "23",
    "openai": "2",
    "pandas": "2.1",
    "psycopg": "3.2",
    "psycopg-pool": "3.3",
    "python-dotenv": "1",
    "streamlit": "1.66.1",
}


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _check_python(errors: list[str]) -> None:
    current = sys.version_info[:2]
    if current < MINIMUM_PYTHON:
        errors.append(
            f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}+ is required; "
            f"found {current[0]}.{current[1]}."
        )
    print(f"[OK] Python {sys.version.split()[0]} ({sys.executable})")


def _check_packages(errors: list[str]) -> None:
    for name, minimum in REQUIRED_PACKAGES.items():
        installed = _version(name)
        if installed is None:
            errors.append(f"Missing package: {name}>={minimum}")
            print(f"[FAIL] {name} is not installed")
            continue
        try:
            supported = Version(installed) >= Version(minimum)
        except InvalidVersion:
            supported = False
        if not supported:
            errors.append(f"{name}>={minimum} is required; found {installed}")
            print(f"[FAIL] {name} {installed} (requires >= {minimum})")
        else:
            print(f"[OK] {name} {installed}")


def _check_dependency_graph(errors: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + result.stderr).strip()
        errors.append(f"pip check failed: {details}")
        print("[FAIL] Installed packages have dependency conflicts")
    else:
        print("[OK] pip dependency graph")


def _configuration_summary() -> None:
    from dotenv import load_dotenv

    env_file = PROJECT_ROOT / ".env"
    load_dotenv(env_file, override=False)
    provider = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
    model = os.getenv("OPENAI_MODEL", "").strip() or "default"
    api_key_configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
    account_secrets = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    local_login_demo = os.getenv("SWEATERCO_LOCAL_REVIEW", "") == "1"
    proactive_demo = os.getenv("PROACTIVE_DEMO_MODE", "").casefold() == "true"
    demo_workspace = os.getenv("PROACTIVE_DEMO_WORKSPACE", "").strip()

    print(f"[INFO] .env present: {env_file.is_file()}")
    print(f"[INFO] model provider: {provider}")
    print(f"[INFO] model: {model}")
    print(f"[INFO] API key configured: {api_key_configured}")
    print(f"[INFO] account secrets present: {account_secrets.is_file()}")
    print(f"[INFO] local login demo mode: {local_login_demo}")
    print(f"[INFO] proactive demo mode: {proactive_demo}")
    if proactive_demo:
        print(f"[INFO] proactive demo workspace: {demo_workspace or 'MISSING'}")
    if provider == "openai" and not api_key_configured:
        print("[WARN] Natural-language chat and speech need OPENAI_API_KEY.")
        print("[WARN] Structured Quick Workflows can still run without it.")
    if local_login_demo:
        print("[INFO] The app will start with disposable local demo accounts.")
    elif not account_secrets.is_file():
        mode = (
            "anonymous chat with local proactive demo"
            if proactive_demo
            else "anonymous, session-only"
        )
        print(f"[INFO] The app will start in {mode} mode.")


def main() -> int:
    os.chdir(PROJECT_ROOT)
    errors: list[str] = []
    print("SweaterCo local environment check")
    print("=" * 35)
    _check_python(errors)
    _check_packages(errors)
    _check_dependency_graph(errors)
    _configuration_summary()
    if errors:
        print("\nEnvironment is not ready:")
        for error in errors:
            print(f"- {error}")
        print("\nRun .\\setup.ps1 from the project root, then retry.")
        return 1
    print("\nEnvironment is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
