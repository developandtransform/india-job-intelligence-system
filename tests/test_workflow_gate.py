"""Safety patch 1: scheduled production is gated by the repository variable ENABLE_SCHEDULED_PRODUCTION; manual runs default to
dry run. GitHub expression == is case-INsensitive, so a preliminary `gate` job compares the raw value with a shell test. Here the
REAL gate script and the Run step script are executed with bash (the Run step with a fake `python`); the remaining job `if:` is
evaluated by a tiny interpreter over the gate output, which is only ever "true" or "false". (Offline simulation - the real GitHub evaluation is not tested.)"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not found (install Git for Windows or run in WSL/Ubuntu); these tests execute the real workflow shell scripts")
ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows/daily_jobs.yml"
wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
JOB = wf["jobs"]["run"]


GATE = wf["jobs"]["gate"]


def gate_output(value, tmp_path) -> str:
    """Execute the REAL shell script of the gate step with the variable passed as GitHub would (raw, in the environment).
    value=None means the variable is not defined, which GitHub passes as an empty string."""
    step = next(s for s in GATE["steps"] if s.get("id") == "check")
    assert step["env"]["ENABLE_SCHEDULED_PRODUCTION"] == "${{ vars.ENABLE_SCHEDULED_PRODUCTION }}"
    out_file = tmp_path / "github_output"
    out_file.write_text("")
    env = {**os.environ, "GITHUB_OUTPUT": out_file.as_posix(), "ENABLE_SCHEDULED_PRODUCTION": "" if value is None else value}
    subprocess.run([BASH, "-c", step["run"]], capture_output=True, text=True, cwd=tmp_path, env=env, check=True)
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("enabled=")
    return lines[0].split("=", 1)[1]


def job_runs(event: str, gate: str) -> bool:
    """Evaluate the production job's `if:` given the event and the gate job's output (the only expression left)."""
    expr = JOB["if"]
    assert JOB["needs"] == "gate" and "vars." not in expr, "production must depend on the gate, not on a case-insensitive vars comparison"
    ctx = {"github.event_name": event, "needs.gate.outputs.scheduled_enabled": gate}

    def term(t: str) -> bool:
        m = re.fullmatch(r"\s*([\w.]+)\s*==\s*'([^']*)'\s*", t)
        assert m, f"unsupported expression part: {t!r}"
        return ctx.get(m.group(1), "") == m.group(2)

    assert "&&" not in expr and "!=" not in expr
    return any(term(t) for t in expr.split("||"))


def scheduled_runs(value, tmp_path) -> bool:
    return job_runs("schedule", gate_output(value, tmp_path))


def test_schedule_is_kept_and_unchanged():
    assert wf[True if True in wf else "on"]["schedule"][0]["cron"] == "30 3 * * *"


@needs_bash
def test_scheduled_variable_missing_skips_production(tmp_path):
    assert gate_output(None, tmp_path) == "false" and scheduled_runs(None, tmp_path) is False


@needs_bash
@pytest.mark.parametrize("value", ["false", "FALSE", "TRUE", "True", "true ", " true", "", "1", "yes", "tru", "true\n", "'true'"])
def test_scheduled_variable_not_exactly_lowercase_true_skips_production(value, tmp_path):
    assert gate_output(value, tmp_path) == "false"
    assert scheduled_runs(value, tmp_path) is False


@needs_bash
def test_scheduled_variable_exact_lowercase_true_allows_production(tmp_path):
    assert gate_output("true", tmp_path) == "true" and scheduled_runs("true", tmp_path) is True


@needs_bash
def test_manual_run_is_always_allowed_whatever_the_variable(tmp_path):
    for v in (None, "false", "TRUE", "true"):
        assert job_runs("workflow_dispatch", gate_output(v, tmp_path)) is True


def test_gate_job_is_cheap_read_only_and_has_no_secrets():
    text = yaml.dump(GATE)
    assert "secrets." not in text and "checkout" not in text and GATE["timeout-minutes"] <= 5


def test_manual_input_defaults_to_dry_run_and_production_is_explicit():
    inp = wf[True if True in wf else "on"]["workflow_dispatch"]["inputs"]["mode"]
    assert inp["type"] == "choice" and inp["default"] == "dry-run" and inp["options"] == ["dry-run", "production"]


def run_step_mode(event: str, mode: str, tmp_path) -> str:
    """Run the real shell script of the 'Run' step with a fake python that only prints its arguments."""
    step = next(s for s in JOB["steps"] if s.get("name") == "Run")
    script = "python() { echo \"PYTHON $*\"; }\n" + step["run"]
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, cwd=tmp_path,
                         env={**os.environ, "EVENT_NAME": event, "RUN_MODE": mode}).stdout
    return next(l for l in out.splitlines() if l.startswith("PYTHON"))


@needs_bash
def test_run_step_decisions(tmp_path):
    assert run_step_mode("workflow_dispatch", "dry-run", tmp_path) == "PYTHON main.py run --dry-run"
    assert run_step_mode("workflow_dispatch", "", tmp_path) == "PYTHON main.py run --dry-run"          # default / empty -> dry run
    assert run_step_mode("workflow_dispatch", "anything-else", tmp_path) == "PYTHON main.py run --dry-run"
    assert run_step_mode("workflow_dispatch", "production", tmp_path) == "PYTHON main.py run"
    assert run_step_mode("schedule", "", tmp_path) == "PYTHON main.py run"                                # only reachable when enabled
    assert run_step_mode("push", "production", tmp_path) == "PYTHON main.py run --dry-run"             # unknown trigger -> safe


def test_gate_does_not_touch_the_separate_test_workflow():
    t = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
    assert "if" not in t["jobs"][next(iter(t["jobs"]))]
    assert "schedule" not in t[True if True in t else "on"]


def test_workflow_never_hardcodes_enabling_the_variable():
    text = WF.read_text(encoding="utf-8")
    assert not re.search(r"ENABLE_SCHEDULED_PRODUCTION\s*[:=]\s*[\"']?true", text)          # only ever read from vars.*
    assert text.count("vars.ENABLE_SCHEDULED_PRODUCTION") == 1                             # read once, by the gate job
