"""Verify status gating with tiny local fixtures; no real Slurm jobs or BAMs."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
# Import production templates when running this test as a standalone script.
sys.path.insert(0, str(REPO / "templates"))
TEMPLATE = REPO / "templates/status_check.sh"
CHECKER = REPO / "templates/check_workflow_status.py"
spec = importlib.util.spec_from_file_location("status_checker", CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


# Each public test exercises a distinct status or failure scenario.
# pylint: disable-next=too-many-public-methods
class StatusHandoffTest(unittest.TestCase):
    """Verify status reports and manifest handoff for local and Slurm jobs."""
    def setUp(self):
        """Create isolated files and metadata for each test."""
        # addCleanup also releases the fixture when setup or assertions fail.
        # pylint: disable-next=consider-using-with
        self.tmp = tempfile.TemporaryDirectory(prefix="manifest-handoff-")
        self.last_stdout = ""
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "aa" / "child work's directory"
        self.work.mkdir(parents=True)
        self.records = self.work / "PIPELINEEXITSTATUS"
        self.records.mkdir()
        (self.records / "expected_pipelines.json").write_text('["call-gSNP"]', encoding="utf-8")
        (self.records / "call-gSNP.0").touch()
        self.destination = self.root / "status-work"
        self.destination.mkdir()
        self.manifest = self.work / "OUTPUTMANIFEST/output_manifest.json"
        self.manifest.parent.mkdir()
        self.identifier = "sample's identifier"
        self.receipt = {'schema_version': 1, 'run_id': "aa_" + self.work.name,
                            'workflow_identifier': self.identifier, 'patient': "P", 'exit_code': 0}
        self.write_receipt()
        self.published = self.root / "published"
        self.published.mkdir()
        bam = self.published / "test.bam"
        bam.write_text("fixture", encoding="utf-8")
        identity = {k: v for k, v in self.receipt.items() if k != "exit_code"}
        self.payload = dict(identity, publish_root=str(self.published), entries=[
            dict(identity, generated=True, entry_id=str(bam), published_bam=str(bam),
                 published_indexes=[], published_checksums=[], published_file_sizes={str(bam): 7})])
        self.write_manifest()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.command("squeue", "exit 0\n")
        self.command("sacct", "printf '123|COMPLETED|0:0\\n'\n")

    def command(self, name, body):
        """Create an executable stand-in for a scheduler command."""
        target = self.bin / name
        target.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        target.chmod(0o755)

    def write_receipt(self):
        """Write the current child completion receipt."""
        (self.work / "child_completion.json").write_text(json.dumps(self.receipt), encoding="utf-8")

    def write_manifest(self):
        """Write the current output manifest."""
        self.manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def check(self, submission="-1"):
        """Run status verification against the fixture files."""
        return checker.check(self.work, submission, self.identifier, self.manifest,
                             self.destination, attempts=2, interval=0)

    def assert_status(self, expected, report):
        """Check the reported status and whether a manifest was handed off."""
        self.assertEqual(report["status"], expected, report)
        self.assertEqual((self.destination / "output_manifest.json").exists(),
            expected == "success")

    def run_shell(self, submission="-1", executable=CHECKER):
        """Run the status shell template and read its JSON report."""
        script = TEMPLATE.read_text(encoding="utf-8")
        for name, value in {
            "quoted_work_dir": str(self.work), "quoted_sbatch_ret": submission,
            "quoted_identifier": self.identifier, "quoted_output_manifest_path": str(self.manifest),
            "quoted_status_checker": str(executable),
        }.items():
            script = script.replace("!{" + name + "}", shlex.quote(value))
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script], cwd=self.destination,
            env={**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]},
            universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.last_stdout = result.stdout
        return json.loads((self.destination / "workflow_status.json").read_text(encoding="utf-8"))

    def test_direct_success_copies_manifest(self):
        """Verify direct success copies manifest."""
        self.assert_status("success", self.run_shell())
        self.assertEqual((self.destination / "output_manifest.json").read_bytes(),
            self.manifest.read_bytes())

    def test_empty_manifest_can_succeed(self):
        """Verify empty manifest can succeed."""
        self.payload["entries"] = []
        self.write_manifest()
        self.assert_status("success", self.check())

    def test_slurm_success(self):
        """Verify SLURM success."""
        self.assert_status("success", self.run_shell("Submitted batch job 123"))

    def test_missing_manifest(self):
        """Verify missing manifest."""
        self.manifest.unlink()
        self.assert_status("unknown", self.run_shell())

    def test_missing_receipt_removes_stale_handoff(self):
        """Verify missing receipt removes stale handoff."""
        (self.destination / "output_manifest.json").write_text("old handoff", encoding="utf-8")
        (self.work / "child_completion.json").unlink()
        self.assert_status("unknown", self.run_shell())

    def test_child_failure(self):
        """Verify child failure."""
        self.receipt["exit_code"] = 1
        self.write_receipt()
        self.assert_status("failed", self.run_shell())

    def test_wrong_child_invocation(self):
        """Verify wrong child invocation."""
        self.receipt["run_id"] = "another-run"
        self.write_receipt()
        self.assert_status("unknown", self.check())

    def test_missing_pipeline_record(self):
        """Verify missing pipeline record."""
        (self.records / "call-gSNP.0").unlink()
        self.assert_status("unknown", self.check())

    def test_multi_digit_downstream_failure(self):
        """Verify multi digit downstream failure."""
        (self.records / "call-gSNP.10").touch()
        report = self.run_shell()
        self.assert_status("failed", report)
        self.assertEqual(report["reason"].count("Downstream pipelines failed:"), 1)

    def assert_pipeline_failures_reported(self, report, primary_reason):
        """Check that primary and downstream failures appear in the log."""
        self.assert_status("failed", report)
        self.assertIn(primary_reason, report["reason"])
        self.assertIn("Downstream pipelines failed: call-gSNP, call-sSNV", report["reason"])
        self.assertIn(f"{self.identifier}: failed - {report['reason']}", self.last_stdout)

    def test_pipeline_names_printed_after_child_failure(self):
        """Verify pipeline names printed after child failure."""
        self.receipt["exit_code"] = 1
        self.write_receipt()
        (self.records / "call-gSNP.10").touch()
        (self.records / "call-sSNV.2").touch()
        self.assert_pipeline_failures_reported(self.run_shell(), "Child Nextflow exited 1")

    def test_pipeline_names_printed_after_slurm_failure(self):
        """Verify pipeline names printed after SLURM failure."""
        self.command("sacct", "printf '123|FAILED|1:0\\n'\n")
        (self.records / "call-gSNP.10").touch()
        (self.records / "call-sSNV.2").touch()
        self.assert_pipeline_failures_reported(self.run_shell("Submitted batch job 123"),
                                               "Slurm job 123 ended FAILED (1:0)")

    def test_pipeline_names_printed_when_child_receipt_missing(self):
        """Verify pipeline names printed when child receipt missing."""
        (self.work / "child_completion.json").unlink()
        (self.records / "call-gSNP.10").touch()
        (self.records / "call-sSNV.2").touch()
        self.assert_pipeline_failures_reported(self.run_shell(), "child_completion.json")

    def test_missing_or_invalid_inventory_does_not_hide_pipeline_failures(self):
        """Verify missing or invalid inventory does not hide pipeline failures."""
        (self.records / "call-gSNP.10").touch()
        (self.records / "call-sSNV.2").touch()
        inventory = self.records / "expected_pipelines.json"
        for contents in (None, "not json", '{}'):
            with self.subTest(inventory=contents):
                if contents is None:
                    inventory.unlink()
                else:
                    inventory.write_text(contents, encoding="utf-8")
                report = self.run_shell()
                self.assert_pipeline_failures_reported(report, "Downstream pipelines failed:")
                self.assertTrue("expected_pipelines.json" in report["reason"] or
                                "Invalid expected pipeline inventory" in report["reason"])

    def test_malformed_and_missing_records_do_not_hide_pipeline_failures(self):
        """Verify malformed and missing records do not hide pipeline failures."""
        (self.records / "invalid-record").touch()
        (self.records / "call-gSNP.10").touch()
        (self.records / "call-sSNV.2").touch()
        (self.records / "expected_pipelines.json").write_text('["call-gSNP", "call-sCNA"]',
            encoding="utf-8")
        report = self.run_shell()
        self.assert_pipeline_failures_reported(report, "Unrecognized pipeline status record:")
        self.assertIn("Missing downstream status records: call-sCNA", report["reason"])

    def test_unknown_slurm_evidence_does_not_hide_pipeline_failures(self):
        """Verify unknown SLURM evidence does not hide pipeline failures."""
        (self.records / "call-gSNP.10").touch()
        with patch.object(checker, "command", return_value=None):
            report = self.check("Submitted batch job 123")
        self.assert_status("failed", report)
        self.assertIn("No unambiguous successful Slurm accounting record", report["reason"])
        self.assertIn("Downstream pipelines failed: call-gSNP", report["reason"])

    def test_missing_status_directory_does_not_mask_child_failure(self):
        """Verify missing status directory does not mask child failure."""
        self.receipt["exit_code"] = 1
        self.write_receipt()
        for path in self.records.iterdir():
            path.unlink()
        self.records.rmdir()
        report = self.run_shell()
        self.assert_status("failed", report)
        self.assertIn("Child Nextflow exited 1", report["reason"])
        self.assertIn("Could not read pipeline status directory", report["reason"])

    def test_missing_pipeline_inventory(self):
        """Verify missing pipeline inventory."""
        (self.records / "expected_pipelines.json").unlink()
        self.assert_status("unknown", self.check())

    def test_partial_publication(self):
        """Verify partial publication."""
        (self.published / "test.bam").write_text("part", encoding="utf-8")
        self.assert_status("unknown", self.check())

    def test_missing_sidecar(self):
        """Verify missing sidecar."""
        sidecar = str(self.published / "test.bam.bai")
        self.payload["entries"][0]["published_indexes"].append(sidecar)
        self.payload["entries"][0]["published_file_sizes"][sidecar] = 7
        self.write_manifest()
        self.assert_status("unknown", self.check())

    def test_manifest_identity_mismatch(self):
        """Verify manifest identity mismatch."""
        self.payload["patient"] = "another-patient"
        self.write_manifest()
        self.assert_status("unknown", self.check())

    def test_duplicate_entries(self):
        """Verify duplicate entries."""
        self.payload["entries"] *= 2
        self.write_manifest()
        self.assert_status("unknown", self.check())

    def test_published_symlink_outside_output_root(self):
        """Verify published symlink outside output root."""
        outside = self.root / "original-input.bam"
        outside.write_text("fixture", encoding="utf-8")
        bam = self.published / "test.bam"
        bam.unlink()
        bam.symlink_to(outside)
        self.assert_status("unknown", self.check())

    def test_partial_copy(self):
        """Verify partial copy."""
        def failed_copy(_source, destination):
            Path(destination).write_text("partial", encoding="utf-8")
            raise OSError("copy failed")
        with patch.object(checker.shutil, "copyfile", side_effect=failed_copy):
            self.assert_status("unknown", self.check())

    def test_failed_rename(self):
        """Verify failed rename."""
        with patch.object(Path, "replace", side_effect=OSError("rename failed")):
            self.assert_status("unknown", self.check())

    def test_checker_crash_does_not_emit_manifest(self):
        """Verify checker crash does not emit manifest."""
        (self.destination / "output_manifest.json").write_text("old", encoding="utf-8")
        self.assert_status("unknown", self.run_shell(executable=self.root / "absent.py"))

    def test_failed_slurm_allocation(self):
        """Verify failed SLURM allocation."""
        for state, code in [("FAILED", "1:0"), ("CANCELLED by 123", "0:15"),
                            ("TIMEOUT", "0:0"), ("COMPLETED", "0:9")]:
            with self.subTest(state=state, code=code), patch.object(
                    checker, "command",
                        side_effect=["", f"123|{state}|{code}\n123.batch|COMPLETED|0:0\n"]):
                self.assert_status("failed", self.check("Submitted batch job 123"))

    def test_batch_success_alone_is_insufficient(self):
        """Verify batch success alone is insufficient."""
        with patch.object(checker, "command", side_effect=["", "123.batch|COMPLETED|0:0\n", ""]):
            self.assert_status("unknown", self.check("Submitted batch job 123"))

    def test_slurm_accounting_delay(self):
        """Verify SLURM accounting delay."""
        with patch.object(checker, "command", side_effect=["123\n", "", "", "123|COMPLETED|0:0\n"]):
            self.assert_status("success", self.check("Submitted batch job 123"))

    def test_slurm_queue_error_is_not_completion(self):
        """Verify SLURM queue error is not completion."""
        with patch.object(checker, "command", return_value=None):
            self.assert_status("unknown", self.check("Submitted batch job 123"))

    def test_finished_job_removed_from_queue_can_be_verified_by_accounting(self):
        """Verify finished job removed from queue can be verified by accounting."""
        with patch.object(checker, "command", side_effect=[None, None, "123|COMPLETED|0:0\n"]):
            self.assert_status("success", self.check("Submitted batch job 123"))

    def test_unknown_submission(self):
        """Verify unknown submission."""
        self.assert_status("unknown", self.run_shell("submission failed"))

    def test_wrapper_completion_report(self):
        """Verify wrapper completion report."""
        wrapper = runpy.run_path(str(REPO / "templates/nextflow-wrapper"))
        target = self.root / "receipt.json"
        args = SimpleNamespace(task_hash="aa_hash", workflow_identifier="sample", patient="patient")
        wrapper["write_completion_report"](target, args, 17)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
            {'schema_version': 1, 'run_id': "aa_hash",
                         'workflow_identifier': "sample", 'patient': "patient", 'exit_code': 17})
        self.assertFalse(target.with_name("receipt.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
