#!/usr/bin/env python3
"""Unit tests for report.py — the completion contract, pure code. Run:
    python3 tests/test_report.py

The semantics under test are the ones locked when the contract was designed:
complete ONLY on total success; the delta returns ONLY knowledge whose fingerprint
was not passed in (from either the run tier or the global tier); every asked task
gets a status, including not-attempted.
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import planfile  # noqa: E402
import report  # noqa: E402


def plan(*tasks):
    return {"schema": planfile.SCHEMA_VERSION, "slug": "s", "cycle": 0,
            "disposition": "tasks", "rationale": "r", "question": None,
            "tasks": list(tasks)}


def task(tid, serves=None):
    return {"id": tid, "method": f"method {tid}", "description": f"do {tid}",
            "success_criterion": f"{tid} done", "intent_link": f"{tid} advances it",
            "depends_on": [], "status": "pending",
            **({"serves": serves} if serves is not None else {})}


def res(tid, verdict, evidence="ev", learnings=None):
    r = {"id": tid, "method": f"method {tid}", "verdict": verdict, "evidence": evidence}
    if learnings:
        r["learnings"] = learnings
    return r


def dod(*items, gid="R1"):
    """A parse_dod-shaped dict: items = (rid, marker) pairs."""
    return {"groups": [{"id": gid,
                        "items": [{"id": rid, "marker": m} for rid, m in items]}]}


class StatusSemantics(unittest.TestCase):
    def test_complete_only_on_total_success(self):
        r = report.completion_report(plan(task("t1"), task("t2")),
                                     [res("t1", "worked"), res("t2", "worked")])
        self.assertEqual(r["status"], "complete")

    def test_one_failure_means_partial(self):
        r = report.completion_report(plan(task("t1"), task("t2")),
                                     [res("t1", "worked"), res("t2", "failed")])
        self.assertEqual(r["status"], "partial")

    def test_nothing_done_means_failed(self):
        r = report.completion_report(plan(task("t1")), [res("t1", "failed")])
        self.assertEqual(r["status"], "failed")
        r = report.completion_report(plan(task("t1")), [])
        self.assertEqual(r["status"], "failed")

    def test_pending_requirement_blocks_complete_even_when_all_tasks_done(self):
        d = dod(("R1.1", "○"), ("R1.2", "○"))
        r = report.completion_report(plan(task("t1", serves=["R1.1"])),
                                     [res("t1", "worked")], dod_parsed=d)
        self.assertEqual(r["requirements"], {"R1.1": "met", "R1.2": "pending"})
        self.assertEqual(r["status"], "partial")

    def test_no_dod_means_no_requirements_key(self):
        r = report.completion_report(plan(task("t1")), [res("t1", "worked")])
        self.assertNotIn("requirements", r)


class TaskJoin(unittest.TestCase):
    def test_every_asked_task_gets_a_status(self):
        r = report.completion_report(
            plan(task("t1"), task("t2"), task("t3")),
            [res("t1", "worked", evidence="receipt-1"), res("t2", "skipped")])
        statuses = {t["id"]: t["status"] for t in r["tasks"]}
        self.assertEqual(statuses, {"t1": "done", "t2": "not-attempted",
                                    "t3": "not-attempted"})
        self.assertEqual(r["tasks"][0]["evidence"], "receipt-1")

    def test_serves_travels_into_the_report(self):
        r = report.completion_report(plan(task("t1", serves=["R1.1", "R2.1"])),
                                     [res("t1", "worked")])
        self.assertEqual(r["tasks"][0]["serves"], ["R1.1", "R2.1"])


class RequirementsRollup(unittest.TestCase):
    def test_blocked_when_every_serving_task_failed(self):
        d = dod(("R1.1", "○"))
        r = report.completion_report(plan(task("t1", serves=["R1.1"])),
                                     [res("t1", "failed")], dod_parsed=d)
        self.assertEqual(r["requirements"]["R1.1"], "blocked")

    def test_dod_markers_win_over_serving(self):
        d = dod(("R1.1", "✓"), ("R1.2", "~"))
        r = report.completion_report(plan(task("t1", serves=["R1.1", "R1.2"])),
                                     [res("t1", "failed")], dod_parsed=d)
        self.assertEqual(r["requirements"], {"R1.1": "met", "R1.2": "waived"})
        # nothing unmet remains, but the task itself failed → not complete
        self.assertEqual(r["status"], "failed")

    def test_group_id_serving_counts_for_leaves(self):
        d = dod(("R1.1", "○"), gid="R1")
        r = report.completion_report(plan(task("t1", serves=["R1"])),
                                     [res("t1", "worked")], dod_parsed=d)
        self.assertEqual(r["requirements"]["R1.1"], "met")


class RecordFold(unittest.TestCase):
    def test_fold_shapes_and_namespaces(self):
        p = plan(task("t1"), task("t2"), task("t3"))
        records = report.records_from_results(
            p, [res("t1", "failed", evidence="boom", learnings=["port 443 blocked"]),
                res("t2", "worked"), res("t3", "skipped")], cycle=2)
        by_kind = {}
        for rec in records:
            by_kind.setdefault(rec["kind"], []).append(rec)
        dead = by_kind["dead-end"][0]
        self.assertEqual(dead["fp"], planfile.fp("method t1"))
        self.assertIn("Tried method t1: failed — boom", dead["text"])
        facts = {f["text"] for f in by_kind["fact"]}
        self.assertIn("Done method t2: ev", facts)
        self.assertIn("port 443 blocked", facts)
        ok_fact = next(f for f in by_kind["fact"] if f["text"].startswith("Done"))
        self.assertEqual(ok_fact["fp"], planfile.fp("ok method t2"))
        # skipped folds nothing
        self.assertEqual(len(records), 3)
        self.assertTrue(all(rec["cycle"] == 2 for rec in records))


class DeltaDiscipline(unittest.TestCase):
    def test_delta_excludes_fps_passed_in_from_either_tier(self):
        p = plan(task("t1"), task("t2"))
        results = [res("t1", "failed"), res("t2", "worked")]
        # t1's dead-end fp arrives via the "run tier", t2's ok-fp via the "global tier"
        knowledge_in = {planfile.fp("method t1"), planfile.fp("ok method t2")}
        r = report.completion_report(p, results, knowledge_in_fps=knowledge_in)
        self.assertEqual(r["delta"], [])
        r = report.completion_report(p, results)
        self.assertEqual({rec["fp"] for rec in r["delta"]}, knowledge_in)


class PriorState(unittest.TestCase):
    def _seed(self, d):
        with open(os.path.join(d, "ledger.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "fact", "text": "run fact",
                                 "fp": planfile.fp("run fact")}) + "\n")
            fh.write("garbage line\n")
        gpath = os.path.join(d, "global.jsonl")
        with open(gpath, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "fact", "text": "global fact",
                                 "fp": planfile.fp("global fact")}) + "\n")
        return gpath

    def test_load_prior_reads_both_tiers_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            gpath = self._seed(d)
            records = report.load_prior(d, global_path=gpath)
            sources = {r["text"]: r["source"] for r in records}
            self.assertEqual(sources, {"run fact": "run", "global fact": "global"})
            fps = report.knowledge_fps(records)
            self.assertIn(planfile.fp("run fact"), fps)
            self.assertIn(planfile.fp("global fact"), fps)

    def test_saved_report_deltas_feed_the_next_round(self):
        with tempfile.TemporaryDirectory() as d:
            r = report.completion_report(plan(task("t1")), [res("t1", "failed")])
            cycle_dir = os.path.join(d, "c0")
            path = report.save_report(cycle_dir, r)
            self.assertEqual(path, report.report_path(cycle_dir))
            records = report.load_prior(d)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "run")
            self.assertEqual(records[0]["fp"], planfile.fp("method t1"))
            # the fp now suppresses the same dead-end in the next cycle's delta
            nxt = report.completion_report(plan(task("t1")), [res("t1", "failed")],
                                           knowledge_in_fps=report.knowledge_fps(records))
            self.assertEqual(nxt["delta"], [])

    def test_load_prior_tolerates_absent_dirs_and_garbage_reports(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(report.load_prior(os.path.join(d, "nope")), [])
            cdir = os.path.join(d, "c1")
            os.makedirs(cdir)
            with open(report.report_path(cdir), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(report.load_prior(d), [])

    def test_knowledge_fps_falls_back_to_text(self):
        fps = report.knowledge_fps([{"text": "no fp here"}, {}, {"fp": "abc"}])
        self.assertEqual(fps, {planfile.fp("no fp here"), "abc"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
