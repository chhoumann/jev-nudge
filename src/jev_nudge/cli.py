import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from jev_nudge.hook import Config, audit, judge, run_hook
from jev_nudge.transcript import build_state

STATUS = "Jev Nudge: checking unfinished work"


def install(target: Path, config_path: Path, remove=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_text() if target.exists() else None
    data = json.loads(original) if original else {"hooks": {}}
    groups = data.setdefault("hooks", {}).setdefault("Stop", [])
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if h.get("statusMessage") != STATUS]
    groups[:] = [g for g in groups if g.get("hooks")]
    if not remove:
        command = shlex.join(
            [
                str(Path(sys.executable).parent / "jev-nudge"),
                "hook",
                "--config",
                str(config_path.resolve()),
            ]
        )
        groups.append(
            {
                "hooks": [
                    {"type": "command", "command": command, "timeout": 20, "statusMessage": STATUS}
                ]
            }
        )
    rendered = json.dumps(data, indent=2) + "\n"
    if rendered == original:
        return
    if original:
        backup = target.with_name(target.name + f".backup-{time.time_ns()}")
        backup.write_text(original)
        backup.chmod(target.stat().st_mode & 0o777)
    temp = target.with_suffix(".tmp")
    temp.write_text(rendered)
    temp.chmod(target.stat().st_mode & 0o777 if target.exists() else 0o600)
    temp.replace(target)


def main():
    parser = argparse.ArgumentParser(description="Jev-powered Codex stop checks")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("hook", "check", "install", "uninstall", "doctor", "eval"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, required=True)
        if name in ("install", "uninstall"):
            scope = p.add_mutually_exclusive_group(required=True)
            scope.add_argument("--project", type=Path)
            scope.add_argument("--user", action="store_true")
        if name == "check":
            p.add_argument("transcript", type=Path)
            p.add_argument("--last-message", required=True)
        if name == "eval":
            p.add_argument("cases", type=Path)
    args = parser.parse_args()
    config = None
    try:
        config = Config.load(args.config.resolve())
        if args.command == "hook":
            print(json.dumps(run_hook(json.load(sys.stdin), config)))
        elif args.command in ("install", "uninstall"):
            base = (
                args.project / ".codex"
                if args.project
                else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            )
            target = base / "hooks.json"
            install(target, args.config, args.command == "uninstall")
            print(f"Updated {target}")
            if args.command == "install":
                print("Review and trust this hook in Codex /hooks before using it.")
        elif args.command == "check":
            state = build_state(args.transcript, args.last_message, config.api_key())
            result = judge(state, config)
            print(
                json.dumps({**result, "nudge": result["probability"] >= config.threshold}, indent=2)
            )
        elif args.command == "doctor":
            config.api_key()
            result = judge(
                {"earlier_user_requests": ["Say pong"], "last_assistant_message": "pong"}, config
            )
            print(json.dumps({"ok": True, "enabled": config.enabled, **result}, indent=2))
        elif args.command == "eval":
            failures = 0
            for case in json.loads(args.cases.read_text()):
                result = judge(case["state"], config)
                actual = result["probability"] >= config.threshold
                ok = actual == case["expected_nudge"]
                failures += not ok
                print(
                    json.dumps(
                        {
                            "case": case["name"],
                            "passed": ok,
                            "expected_nudge": case["expected_nudge"],
                            **result,
                        }
                    ),
                    flush=True,
                )
            sys.exit(bool(failures))
    except Exception as error:
        # Hook failures must not hold the user's agent open or reveal HTTP bodies.
        detail = {"decision": "allow", "reason": "error", "error_type": type(error).__name__}
        if args.command == "hook":
            if config:
                try:
                    audit(config, detail)
                except OSError:
                    pass
            print("{}")
            print(f"jev-nudge: skipped ({type(error).__name__})", file=sys.stderr)
        else:
            print(f"jev-nudge: {type(error).__name__}: operation failed", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
