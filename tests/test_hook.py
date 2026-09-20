import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jev_nudge.cli import install
from jev_nudge.hook import NUDGE, Config, run_hook
from jev_nudge.transcript import MAX_STATE_BYTES, build_state


def transcript(path, pairs):
    rows = []
    for role, text in pairs:
        rows.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    path = tmp_path / "transcript.jsonl"
    transcript(
        path, [("user", "Create result.txt and verify it"), ("assistant", "I'll create it next.")]
    )
    config = Config(state_dir=str(tmp_path / "state"))
    event = {
        "session_id": "test-session",
        "turn_id": "turn-1",
        "hook_event_name": "Stop",
        "transcript_path": str(path),
        "last_assistant_message": "I'll create it next.",
        "stop_hook_active": False,
    }
    return config, event


def test_nudge_limits_dedup_and_new_user_turn(setup):
    config, event = setup
    with patch("jev_nudge.hook.judge", return_value={"probability": 0.9}) as judge:
        assert run_hook(event, config) == {"decision": "block", "reason": NUDGE}
        assert run_hook(event, config) == {}
        assert judge.call_count == 1
        event["stop_hook_active"] = True
        for n in range(2, 4):
            event["turn_id"] = f"continuation-{n}"
            assert run_hook(event, config)["decision"] == "block"
        event["turn_id"] = "continuation-4"
        assert run_hook(event, config) == {}
        assert judge.call_count == 3
        event.update(turn_id="new-user-turn", stop_hook_active=False)
        assert run_hook(event, config)["decision"] == "block"


@pytest.mark.parametrize(
    "probability,expected", [(0.499, {}), (0.5, {"decision": "block", "reason": NUDGE})]
)
def test_threshold(setup, probability, expected):
    config, event = setup
    with patch("jev_nudge.hook.judge", return_value={"probability": probability}):
        assert run_hook(event, config) == expected


def test_disable_and_non_stop_never_call_api(setup, monkeypatch):
    config, event = setup
    with patch("jev_nudge.hook.judge") as judge:
        monkeypatch.setenv("JEV_NUDGE_DISABLED", "1")
        assert run_hook(event, config) == {}
        monkeypatch.delenv("JEV_NUDGE_DISABLED")
        event["hook_event_name"] = "Interrupt"
        assert run_hook(event, config) == {}
        assert not judge.called


def test_history_keeps_original_goal_steering_and_final(tmp_path):
    path = tmp_path / "transcript.jsonl"
    pairs = [("user", "Implement the export and verify the download")]
    pairs += [("assistant", "long progress " * 2000)] * 100
    pairs += [("user", "What did you find?"), ("assistant", "Found the issue. I'll fix it next.")]
    transcript(path, pairs)
    state = build_state(path, pairs[-1][1])
    assert state["earlier_user_requests"][0]["text"] == pairs[0][1]
    assert state["recent_messages"][-2]["text"] == "What did you find?"
    assert state["last_assistant_message"] == pairs[-1][1]
    assert state["context_truncated"]
    assert len(json.dumps(state, ensure_ascii=False).encode()) <= MAX_STATE_BYTES


def test_filters_internal_content_and_redacts(tmp_path):
    path = tmp_path / "transcript.jsonl"
    transcript(
        path,
        [
            ("user", "Fix export API_KEY=verysecret"),
            ("assistant", "Bearer hiddensecret key-from-env"),
        ],
    )
    with path.open("a") as file:
        file.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"text": "INTERNAL CONFIG"}, {"text": "Keep the filename"}],
                        "internal_chat_message_metadata_passthrough": {
                            "content_item_kinds": ["environment_context", "user.text"]
                        },
                    },
                }
            )
            + "\n"
        )
        file.write('{"partial":')
    state = json.dumps(build_state(path, "key-from-env", "key-from-env"))
    assert "INTERNAL CONFIG" not in state
    assert "Keep the filename" in state
    for secret in ("verysecret", "hiddensecret", "key-from-env"):
        assert secret not in state


def test_installer_preserves_other_hooks_and_is_idempotent(tmp_path):
    target = tmp_path / "hooks.json"
    other = {"type": "command", "command": "other-hook"}
    original = {"hooks": {"Stop": [{"hooks": [other]}], "SessionStart": []}}
    target.write_text(json.dumps(original))
    config = tmp_path / "config.json"
    install(target, config)
    once = target.read_text()
    install(target, config)
    assert target.read_text() == once
    assert json.loads(once)["hooks"]["Stop"][0]["hooks"] == [other]
    assert len(list(tmp_path.glob("*.backup-*"))) == 1
    install(target, config, remove=True)
    assert json.loads(target.read_text()) == original


def test_corrupt_config_fails_open_via_executable(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "config.json"
    path.write_text("broken")
    result = subprocess.run(
        [sys.executable, "-m", "jev_nudge.cli", "hook", "--config", str(path)],
        input="{}",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert "JSONDecodeError" in result.stderr


def test_no_request_is_not_classified(tmp_path):
    path = tmp_path / "transcript.jsonl"
    transcript(path, [("assistant", "I'll do it")])
    with pytest.raises(ValueError, match="no user request"):
        build_state(path, "I'll do it")


def test_logs_contain_metadata_only(setup):
    config, event = setup
    with patch("jev_nudge.hook.judge", return_value={"probability": 0.8}):
        run_hook(event, config)
    log = (Path(config.state_dir) / "decisions.jsonl").read_text()
    assert "test-secret" not in log
    assert "Create result.txt" not in log
    assert "I'll create" not in log


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), True, "0.8"])
def test_invalid_api_probability_never_nudges(setup, probability):
    import io

    from jev_nudge.hook import judge

    config, _ = setup
    body = {"model": "jev-1.13.0", "answers": {"nudge": {"type": "noul", "noul": probability}}}
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
        with pytest.raises(ValueError, match="probability"):
            judge({}, config)


def test_timeout_fails_open_and_does_not_leak_credentials(setup, monkeypatch, capsys, tmp_path):
    import io
    import sys

    from jev_nudge.cli import main

    config, event = setup
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"state_dir": config.state_dir}))
    monkeypatch.setattr(sys, "argv", ["jev-nudge", "hook", "--config", str(config_path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    with patch("jev_nudge.hook.judge", side_effect=TimeoutError("test-secret")):
        main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {}
    assert "TimeoutError" in output.err
    assert "test-secret" not in output.err
    assert "test-secret" not in (Path(config.state_dir) / "decisions.jsonl").read_text()


def test_compaction_summary_is_retained(tmp_path):
    path = tmp_path / "transcript.jsonl"
    transcript(path, [("user", "Implement export"), ("assistant", "Investigating")])
    with path.open("a") as file:
        file.write(
            json.dumps(
                {
                    "type": "compacted",
                    "payload": {"message": "Outstanding: fix CSV escaping and verify the download"},
                }
            )
            + "\n"
        )
    state = build_state(path, "I will continue next")
    assert state["earlier_user_requests"][-1]["role"] == "summary"
    assert "CSV escaping" in state["earlier_user_requests"][-1]["text"]


def test_installer_preserves_private_file_permissions(tmp_path):
    target = tmp_path / "hooks.json"
    target.write_text('{"hooks":{}}')
    target.chmod(0o600)
    install(target, tmp_path / "config.json")
    assert target.stat().st_mode & 0o777 == 0o600
    assert next(tmp_path.glob("*.backup-*")).stat().st_mode & 0o777 == 0o600
