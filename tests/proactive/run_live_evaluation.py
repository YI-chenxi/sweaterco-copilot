"""Run the frozen proactive cases through P1's real parser without executing actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("cases.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_revision(path: Path = ROOT) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    cli.add_argument("--parser-root", type=Path, default=ROOT)
    cli.add_argument("--parser-revision")
    cli.add_argument("--output-dir", type=Path, required=True)
    args = cli.parse_args()

    parser_root = args.parser_root.resolve()
    sys.path.insert(0, str(parser_root))
    from app.agent.proactive.understanding import ProactiveCommandParser  # noqa: E402
    from dotenv import load_dotenv

    from app.agent.llm_client import create_llm_client  # noqa: E402
    from app.agent.settings import LLMSettings  # noqa: E402

    load_dotenv(ROOT / ".env")
    settings = LLMSettings.from_env(load_dotenv_file=False)
    if settings.provider != "openai":
        cli.error("Set MODEL_PROVIDER=openai for real model evaluation.")
    if not settings.api_key:
        cli.error("OPENAI_API_KEY is required; its value is never written to reports.")

    cases = json.loads(args.cases.read_text(encoding="utf-8"))

    class CountingClient:
        def __init__(self, client):
            self.client = client
            self.calls = 0
            self.failures = 0

        def generate_structured(self, **kwargs):
            self.calls += 1
            try:
                return self.client.generate_structured(**kwargs)
            except Exception:
                self.failures += 1
                raise

    client = CountingClient(create_llm_client(settings))
    parser = ProactiveCommandParser(client)
    started_at = datetime.now(timezone.utc)
    predictions = {}
    errors = {}
    for index, case in enumerate(cases, start=1):
        try:
            result = parser.parse(case["input"], context=case.get("context", {}))
            predictions[case["id"]] = result.to_dict()
        except Exception as exc:  # isolate a single failed case and continue
            predictions[case["id"]] = {
                "command_type": "unknown",
                "task_type": None,
                "parameters": {},
                "target_id": None,
                "missing_fields": [],
                "language": case["language"],
                "reference_text": None,
            }
            errors[case["id"]] = type(exc).__name__
        print(f"Completed {index}/{len(cases)}: {case['id']}", flush=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = args.output_dir / "predictions.json"
    predictions_path.write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("evaluate.py")),
            "--cases",
            str(args.cases.resolve()),
            "--predictions",
            str(predictions_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (args.output_dir / "report.json").write_text(report, encoding="utf-8")

    parser_file = parser_root / "app/agent/proactive/understanding/parser.py"
    prompt_file = parser_root / "app/agent/proactive/understanding/prompts.py"
    metadata = {
        "started_at_utc": started_at.isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "api_key_recorded": False,
        "case_count": len(cases),
        "prediction_count": len(predictions),
        "llm_calls": client.calls,
        "llm_call_failures": client.failures,
        "runner_exceptions": errors,
        "case_sha256": sha256(args.cases),
        "parser_revision": args.parser_revision or git_revision(parser_root),
        "evaluation_repo_revision": git_revision(),
        "parser_source_sha256": sha256(parser_file),
        "prompt_sha256": sha256(prompt_file),
        "worktree_dirty": True,
        "parser_root": str(parser_root),
        "side_effects_enabled": False,
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        key: metadata[key]
        for key in (
            "case_count",
            "prediction_count",
            "llm_calls",
            "llm_call_failures",
            "model",
            "case_sha256",
        )
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
