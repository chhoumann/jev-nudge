"""Run paid, isolated Codex + Jev integration tests without installing global hooks."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jev_nudge.hook import Config  # noqa: E402


def run_case(directory, config, name, prompt, instructions=""):
    fixture = directory / name
    fixture.mkdir()
    subprocess.run(["git", "init", "-q", str(fixture)], check=True)
    values = vars(config).copy()
    values["state_dir"] = str(fixture / "state")
    config_path = fixture / "config.json"
    config_path.write_text(json.dumps(values))
    executable = Path(sys.executable).parent / "jev-nudge"
    subprocess.run(
        [str(executable), "install", "--config", str(config_path), "--project", str(fixture)],
        check=True,
        capture_output=True,
    )
    hook_file = fixture / ".codex/hooks.json"
    command = json.loads(hook_file.read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    hook_file.rename(fixture / "installed-hooks.json")
    args = [
        "codex",
        "exec",
        "--ignore-user-config",
        "--dangerously-bypass-hook-trust",
        "-C",
        str(fixture),
        "-s",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'hooks.Stop=[{hooks=[{type="command",command=' + json.dumps(command) + ",timeout=20}]}]",
        "--json",
    ]
    if instructions:
        args += ["-c", "developer_instructions=" + json.dumps(instructions)]
    args.append(prompt)
    with (fixture / "events.jsonl").open("w") as out, (fixture / "stderr.log").open("w") as err:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Codex exited {result.returncode}; inspect {fixture / 'stderr.log'}")
    decisions = [
        json.loads(line) for line in (fixture / "state/decisions.jsonl").read_text().splitlines()
    ]
    if not decisions or any(row.get("reason") == "error" for row in decisions):
        raise AssertionError("Hook did not complete a real Jev evaluation")
    return fixture, decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = Config.load(args.config.resolve())
    (ROOT / "work").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="smoke-", dir=ROOT / "work"))
    instructions = (
        "This is a controlled premature-stop regression test. On your first response, if no "
        "message beginning Jev Nudge: appears, do not use tools; output exactly: "
        "I will create result.txt and verify its contents next. If a Jev Nudge: continuation "
        "is present, carry out the original user request with tools and verify the file. "
        "Do not use subagents."
    )
    fixture, recovery = run_case(
        directory,
        config,
        "recovery",
        "Create result.txt containing exactly done followed by a newline, and verify its contents.",
        instructions,
    )
    assert (fixture / "result.txt").read_bytes() == b"done\n"
    assert recovery[0]["decision"] == "nudge"
    assert recovery[-1]["decision"] == "allow"
    assert len(recovery) <= config.max_nudges + 1
    _, plan = run_case(
        directory,
        config,
        "plan-only",
        "Give me a three-step plan for migrating a CSV file to SQLite. "
        "Do not implement anything or create files. The plan alone is the full deliverable.",
    )
    assert len(plan) == 1 and plan[0]["decision"] == "allow"
    summary = {
        "passed": True,
        "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "recovery": recovery,
        "plan_only": plan,
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"Evidence: {directory}")


if __name__ == "__main__":
    main()
