"""driver.py — THE one shared end-to-end function every suite's scenarios point at.

`run_scenario(sc, env)` runs the loop the eval exists to prove:

    a real LLM AUTHORS a workflow spec  ->  the engine runs it  ->  it suspends at a human gate
      ->  a real LLM ANSWERS as the human  ->  … repeat around the (possibly cyclic) graph …
      ->  the flow reaches the expected terminal

Grading is BEHAVIORAL, not structural. The harness never prescribes state names, gate options, or step
kinds; it checks bare invariants (the spec validated, the run suspended at least once when a completion
is expected, a terminal was reached within the resume budget, and the terminal matches `sc["expect"]`)
plus the scenario's `evidence` callable over what actually happened:

    ev = {"suspensions": [pending, ...],   # every suspended payload seen (rendered prompts included!)
          "answers":     [answer, ...],    # what the LLM answerer replied, in order
          "final":       <terminal payload>,
          "journal":     [journal.jsonl records],
          "prior_final": <run 1's terminal payload> | None}   # runs:2 scenarios

The rendered `pending.question.prompt` is how state-into-prompt interpolation is observed: canaries live
only in the run's INPUT VALUES (the authoring model never sees them), so a canary in a rendered gate
question proves the model authored a `${...}` hole and the engine filled it. Real models play every role,
so each scenario has an attempts budget; every attempt's evidence lands under `env["artifacts"]`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # skills/resumable-script
SCRIPTS = os.path.join(ROOT, "scripts")
ENGINE = os.path.join(SCRIPTS, "engine.py")
FIXTURES = os.path.join(HERE, "fixtures")
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import answerer                                         # noqa: E402
import authoring                                        # noqa: E402
import wrapper                                          # noqa: E402

EXIT = {"ok": 0, "failed": 1, "usage": 2, "skew": 3,
        "suspended": 10, "in_doubt": 11, "no_autoanswer": 12, "busy": 13}
_EXIT_NAME = {v: k for k, v in EXIT.items()}

DEFAULT_MAX_RESUMES = 6


class Run:
    """One engine CLI invocation result — last stdout line parsed as the status payload."""
    def __init__(self, code, payload, raw):
        self.code = code
        self.payload = payload
        self.raw = raw

    @property
    def status(self):
        return (self.payload or {}).get("status")


def invoke(cmd, flow, state_dir, **opts):
    argv = [sys.executable, ENGINE, cmd, "--flow", flow, "--state-dir", state_dir]
    if "input" in opts:
        argv += ["--input", opts["input"]]
    if "answer" in opts:
        argv += ["--answer", opts["answer"]]
    proc = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ))
    raw = (proc.stdout or "").strip()
    payload = None
    if raw:
        try:
            payload = json.loads(raw.splitlines()[-1])
        except ValueError:
            payload = None
    return Run(proc.returncode, payload, (proc.stderr or "").strip())


class ScenarioFailure(AssertionError):
    """An attempt failed an invariant / evidence check — retried within the attempts budget."""


def _expect(cond, msg):
    if not cond:
        raise ScenarioFailure(msg)


def run_scenario(sc, env):
    """Run one scenario end to end. Passes (returns None) if any attempt satisfies the invariants and
    evidence; raises the last failure otherwise. `env` = {container, artifacts, model?, provider?}."""
    attempts = sc.get("attempts", 2)
    last = None
    art_root = os.path.join(env["artifacts"], "%s_%s" % (sc["suite"], sc["id"]))
    for i in range(attempts):
        art = os.path.join(art_root, "attempt%d" % i)
        os.makedirs(art, exist_ok=True)
        try:
            _run_once(sc, env, art)
            return                                       # a pass on any attempt passes the scenario
        except (ScenarioFailure, authoring.AuthoringError,
                answerer.AnswerError, RuntimeError) as e:
            last = e
            _write(os.path.join(art, "FAILURE.txt"), "%s: %s" % (type(e).__name__, e))
    raise last


def _shape(node):
    """The input's SHAPE — field names and types, never values. The authoring model must know the
    schema to write correct `${$.input...}` paths (it invents field names otherwise), but the canary
    VALUES stay hidden so a rendered canary can only have come through real interpolation."""
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_shape(node[0])] if node else []
    if isinstance(node, bool):
        return "<boolean>"
    if isinstance(node, (int, float)):
        return "<number>"
    return "<%s>" % type(node).__name__ if not isinstance(node, str) else "<string>"


def _authoring_task(sc):
    shape = _shape(json.loads(sc["input"]))
    return (sc["task"]
            + "\n\nINPUT SHAPE — the run's input object has exactly these fields (values arrive at "
              "runtime):\n" + json.dumps(shape, indent=2, sort_keys=True))


def _run_once(sc, env, art):
    # 1. a real LLM authors the spec (validated + repaired inside author_spec)
    spec, author_log = authoring.author_spec(
        _authoring_task(sc), wrapper.REGISTRY, container=env["container"],
        model=env.get("model"), provider=env.get("provider"))
    _write(os.path.join(art, "spec.json"), json.dumps(spec, indent=2))   # order is semantic
    _write(os.path.join(art, "authoring.json"), json.dumps(author_log, indent=2, sort_keys=True))
    os.makedirs(FIXTURES, exist_ok=True)
    _write(os.path.join(FIXTURES, "%s.json" % sc["id"]), json.dumps(spec, indent=2))

    # 2. wrap into a runnable flow (both real-model callers bound; the spec uses what it uses)
    flow = wrapper.write_flow(spec, os.path.join(art, "flow.py"))

    # 3. drive run 1 (and, for cross-run scenarios, run 2 against run 1's final state)
    ev = _drive(sc, env, flow, sc["input"], art, tag="run1")
    prior_final = None
    if sc.get("runs", 1) == 2:
        _expect(ev["final"].code == EXIT["ok"],
                "run 1 of a cross-run scenario must complete; got %s (%s)"
                % (_EXIT_NAME.get(ev["final"].code, ev["final"].code), ev["final"].payload))
        state1 = (ev["final"].payload.get("result") or {}).get("state") or {}
        prior_final = ev["final"].payload
        ev = _drive(sc, env, flow, sc["input2"](state1), art, tag="run2")

    # 4. bare invariants
    final = ev["final"]
    expect = sc.get("expect", "completed")
    if expect == "completed":
        _expect(len(ev["suspensions"]) >= 1,
                "flow never suspended — no human interruption happened (final=%s)" % final.payload)
        _expect(final.code == EXIT["ok"] and final.status == "completed",
                "expected completion, got exit=%s status=%s payload=%s stderr=%s"
                % (_EXIT_NAME.get(final.code, final.code), final.status, final.payload,
                   final.raw[-500:]))
    else:
        _expect(final.code == EXIT["failed"] and final.status == "failed",
                "expected @fail, got exit=%s status=%s payload=%s"
                % (_EXIT_NAME.get(final.code, final.code), final.status, final.payload))

    # 5. the scenario's behavioral evidence
    graded = dict(ev, final=final.payload, prior_final=prior_final)
    _expect(sc["evidence"](graded),
            "evidence check failed: suspensions=%s final=%s"
            % ([p.get("question", {}).get("prompt") for p in ev["suspensions"]], final.payload))


def _drive(sc, env, flow, input_json, art, tag):
    """Run the flow once and answer every suspension via the LLM answerer, within the resume budget.
    Returns {"suspensions": [pending,...], "answers": [...], "final": Run, "journal": [...]}."""
    max_resumes = sc.get("max_resumes", DEFAULT_MAX_RESUMES)
    state_dir = tempfile.mkdtemp(prefix="authoreval-")
    suspensions, answers, answer_log = [], [], []
    try:
        r = invoke("run", flow, state_dir, input=input_json)
        _dump(art, "%s_step0" % tag, r)
        step = 0
        while r.code == EXIT["suspended"] and step < max_resumes:
            pending = (r.payload or {}).get("pending") or {}
            suspensions.append(pending)
            ans, transcript = answerer.answer_gate(
                pending, sc["intent"], answers, container=env["container"],
                model=env.get("model"), provider=env.get("provider"))
            answers.append(ans)
            answer_log.append({"pending": pending, "answer": ans, "transcript": transcript})
            step += 1
            r = invoke("resume", flow, state_dir, answer=json.dumps(ans))
            _dump(art, "%s_step%d" % (tag, step), r)
        _expect(r.code != EXIT["suspended"],
                "still suspended after %d resumes — resume budget exhausted (cycle not converging?)"
                % max_resumes)
        journal = _read_journal(state_dir)
        return {"suspensions": suspensions, "answers": answers, "final": r, "journal": journal}
    finally:
        _write(os.path.join(art, "%s_answers.json" % tag),
               json.dumps(answer_log, indent=2, sort_keys=True))
        _copy(os.path.join(state_dir, "journal.jsonl"), os.path.join(art, "%s_journal.jsonl" % tag))
        shutil.rmtree(state_dir, ignore_errors=True)


def _read_journal(state_dir):
    path = os.path.join(state_dir, "journal.jsonl")
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except (OSError, ValueError):
        pass
    return records


# ------------------------------------------------------------------ small IO helpers
def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _copy(src, dst):
    if os.path.exists(src):
        shutil.copyfile(src, dst)


def _dump(art, tag, run):
    _write(os.path.join(art, "%s.json" % tag),
           json.dumps({"exit": run.code, "status": run.status,
                       "payload": run.payload, "stderr": run.raw}, indent=2, sort_keys=True))
