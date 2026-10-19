"""Test finalization state/recovery with small stand-ins; no BAM conversion."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "templates"))
import finalization_records as records
import check_workflow_status as checker


class FinalizationRecordsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="finalization-records-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.published = self.root / "results/output"
        self.published.mkdir(parents=True)
        self.bam = self.published / "sample.bam"
        paths = [self.bam, Path(str(self.bam) + ".bai"), Path(str(self.bam) + ".sha512")]
        for path in paths:
            path.write_text("fixture")
        self.reference = self.root / "reference.fa"
        self.reference.write_text(">chr1\nACGT\n")
        self.fai = self.root / "reference.fa.fai"
        self.fai.write_text("fixture")
        self.identity = dict(schema_version=1, run_id="aa_child", workflow_identifier="P", patient="P")
        self.entry = dict(self.identity, entry_id=str(self.bam), published_bam=str(self.bam),
                          published_indexes=[str(paths[1])], published_checksums=[str(paths[2])],
                          published_file_sizes={str(p): p.stat().st_size for p in paths},
                          generated=True, stage="recalibrate-BAM", sample="S", aligner="BWA-MEM2",
                          reference_fasta=str(self.reference), reference_fasta_fai=str(self.fai))
        self.manifest = dict(self.identity, publish_root=str(self.published), entries=[self.entry])
        self.work = self.root / "aa/child"
        (self.work / "PIPELINEEXITSTATUS").mkdir(parents=True)
        (self.work / "PIPELINEEXITSTATUS/expected_pipelines.json").write_text("[]")
        (self.work / "child_completion.json").write_text(json.dumps(dict(self.identity, exit_code=0)))
        self.manifest_path = self.work / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.destination = self.root / "status-task"
        self.destination.mkdir()
        self.cram = self.bam.with_suffix('.cram')
        self.crai = Path(str(self.cram) + '.crai')
        self.cram_sha512 = Path(str(self.cram) + '.sha512')
        self.crai_sha512 = Path(str(self.crai) + '.sha512')

    def start(self, delete_source=True, action="cram"):
        record = records.new_record(self.manifest, self.entry, action, delete_source)
        records.save_record(self.manifest, self.entry, record)
        return record

    def convert(self, record):
        self.cram.write_text("CRAM fixture")
        self.crai.write_text("index fixture")
        self.cram_sha512.write_text("CRAM checksum fixture")
        self.crai_sha512.write_text("CRAI checksum fixture")
        result = records.record_conversion(self.manifest, self.entry, record, "test-version",
                                            {"store_md": True, "store_nm": True},
                                            {"method": "flagstat", "bam_flagstat": "10 + 0 in total\n",
                                             "cram_flagstat": "10 + 0 in total\n"})
        records.save_record(self.manifest, self.entry, result)
        return result

    def begin_cleanup(self, record):
        result = records.begin_cleanup(self.manifest, self.entry, record)
        records.save_record(self.manifest, self.entry, result)
        return result

    def finish(self, record):
        for name in record["deletions"]:
            Path(name).unlink()
            record = records.record_deletion(self.manifest, self.entry, record, name)
            records.save_record(self.manifest, self.entry, record)
        return record

    def check(self, expected):
        self.manifest_path.write_text(json.dumps(self.manifest))
        result = checker.check(self.work, "-1", "P", self.manifest_path, self.destination)
        self.assertEqual(result["status"], expected, result)
        self.assertEqual((self.destination / "output_manifest.json").exists(), expected == "success")
        return result

    def test_unexplained_missing_bam_blocks_resume(self):
        self.bam.unlink()
        self.check("unknown")

    def test_pending_conversion_resumes_only_with_intact_sources(self):
        record = self.start()
        self.assertEqual(records.recovery_action(self.manifest, self.entry, record), "convert")
        self.check("success")
        self.bam.unlink()
        self.check("unknown")

    def test_validated_conversion_reused_for_cleanup(self):
        record = self.convert(self.start())
        self.assertEqual(records.recovery_action(self.manifest, self.entry, record), "cleanup")
        self.check("success")

    def test_missing_checksum_blocks_cleanup(self):
        record = self.convert(self.start())
        self.cram_sha512.unlink()
        with self.assertRaisesRegex(records.RecordError, "Required finalization file is missing"):
            records.begin_cleanup(self.manifest, self.entry, record)
        self.assertTrue(self.bam.exists())

    def test_changed_index_checksum_blocks_resume(self):
        self.convert(self.start())
        self.crai_sha512.write_text("changed checksum")
        self.check("unknown")

    def test_legacy_completed_conversion_without_checksums_can_resume(self):
        record = self.finish(self.begin_cleanup(self.convert(self.start())))
        for kind in ('cram_sha512', 'crai_sha512'):
            Path(record['conversion'].pop(kind)['path']).unlink()
        # Simulate a record saved before streaming checksums were introduced.
        records.record_path(self.manifest, self.entry).write_text(json.dumps(record))
        self.check("success")

    def test_missing_bam_before_cleanup_intent_blocks_resume(self):
        self.convert(self.start())
        self.bam.unlink()
        self.check("unknown")

    def test_cleanup_recovers_crash_between_unlink_and_record_update(self):
        record = self.begin_cleanup(self.convert(self.start()))
        self.bam.unlink()
        self.check("success")
        restored = records.load_record(self.manifest, self.entry)
        self.assertEqual(records.recovery_action(self.manifest, self.entry, restored), "cleanup")
        restored = records.record_deletion(self.manifest, self.entry, restored, str(self.bam))
        records.save_record(self.manifest, self.entry, restored)
        for name in restored["deletions"]:
            if Path(name).exists():
                Path(name).unlink()
                restored = records.record_deletion(self.manifest, self.entry, restored, name)
                records.save_record(self.manifest, self.entry, restored)
        self.assertEqual(records.recovery_action(self.manifest, self.entry, restored), "complete")
        self.check("success")

    def test_completed_cleanup_requires_replacement_and_index(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        self.check("success")
        self.crai.unlink()
        self.check("unknown")

    def test_modified_cram_same_size_blocks_resume(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        stat = self.cram.stat()
        os.utime(self.cram, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        self.check("unknown")

    def test_changed_reference_blocks_resume(self):
        self.convert(self.start())
        self.reference.write_text("different reference")
        self.check("unknown")

    def test_changed_manifest_entry_blocks_resume(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        self.entry["aligner"] = "another-aligner"
        self.check("unknown")

    def test_record_from_another_run_is_not_reused(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        path = records.record_path(self.manifest, self.entry)
        data = json.loads(path.read_text())
        data["manifest_identity"]["run_id"] = "different-run"
        path.write_text(json.dumps(data))
        self.check("unknown")

    def test_completed_alignment_deletion_needs_no_cram(self):
        self.entry["stage"] = "align-DNA"
        record = self.finish(self.begin_cleanup(self.start(action="delete_alignment")))
        self.assertEqual(records.recovery_action(self.manifest, self.entry, record), "complete")
        self.assertFalse(self.cram.exists())
        self.check("success")

    def test_retaining_recalibrated_bam_does_not_authorize_its_removal(self):
        record = self.begin_cleanup(self.convert(self.start(delete_source=False)))
        self.assertEqual(record["state"], "complete")
        self.check("success")
        self.bam.unlink()
        self.check("unknown")

    def test_deleted_source_reappearing_blocks_resume(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        self.bam.write_text("fixture")
        self.check("unknown")

    def test_corrupt_record_blocks_resume_even_with_original_bam(self):
        self.start()
        path = records.record_path(self.manifest, self.entry)
        for value in ("partial json", "null", '[]'):
            with self.subTest(value=value):
                path.write_text(value)
                self.check("unknown")

    def test_partial_temporary_record_never_authorizes_missing_bam(self):
        path = records.record_path(self.manifest, self.entry)
        path.parent.mkdir(parents=True)
        path.with_suffix(".json.tmp").write_text('{"state":"complete"')
        self.bam.unlink()
        self.check("unknown")

    def test_atomic_write_failure_preserves_previous_record(self):
        record = self.start()
        path = records.record_path(self.manifest, self.entry)
        before = path.read_bytes()
        with patch.object(records.os, "replace", side_effect=OSError("rename failed")):
            with self.assertRaises(OSError):
                records.save_record(self.manifest, self.entry, record)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".record-*")), [])

    def test_cleanup_cannot_precede_conversion_validation(self):
        with self.assertRaises(records.RecordError):
            records.begin_cleanup(self.manifest, self.entry, self.start())

    def test_cleanup_intent_must_be_saved_before_unlink(self):
        record = self.convert(self.start())
        intent = records.begin_cleanup(self.manifest, self.entry, record)
        self.bam.unlink()
        with self.assertRaisesRegex(records.RecordError, "intent must be persisted"):
            records.save_record(self.manifest, self.entry, intent)
        self.check("unknown")

    def test_invalid_validation_cannot_authorize_deletion(self):
        record = self.convert(self.start())
        for key, value in [("method", "unknown"), ("bam_flagstat", ""), ("cram_flagstat", "9 + 0 in total\n")]:
            invalid = deepcopy(record)
            invalid["conversion"]["validation"][key] = value
            with self.subTest(key=key), self.assertRaises(records.RecordError):
                records.recovery_action(self.manifest, self.entry, invalid)

    def test_stale_state_update_is_rejected(self):
        pending = self.start()
        self.convert(pending)
        with self.assertRaisesRegex(records.RecordError, "reverses recorded progress"):
            records.save_record(self.manifest, self.entry, pending)

    def test_record_location_is_persistent_and_scoped_to_invocation(self):
        path = records.record_path(self.manifest, self.entry)
        self.assertEqual(path.parent, self.root / "results/intermediate/output-manifests/P/aa_child/finalization")
        self.assertNotIn(str(self.work), str(path))
        other = dict(self.manifest, run_id="bb_other")
        self.assertNotEqual(path, records.record_path(other, self.entry))

    def test_resume_reads_only_json_and_filesystem_metadata(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        original_open = Path.open

        def json_only(path, *args, **kwargs):
            self.assertEqual(path.suffix, ".json", "Attempted file-content read: " + str(path))
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", json_only):
            checker.verify_manifest(self.manifest, dict(self.identity, exit_code=0), "P")

    def worker_step(self, step, delete_source=True):
        previous = Path.cwd()
        try:
            os.chdir(self.work)
            records.worker_record_step(self.manifest, self.entry, step, delete_source)
        finally:
            os.chdir(previous)

    def worker_conversion_outputs(self, matching=True):
        self.cram.write_text("CRAM fixture")
        self.crai.write_text("index fixture")
        self.cram_sha512.write_text("CRAM checksum fixture")
        self.crai_sha512.write_text("CRAI checksum fixture")
        (self.work / "samtools.version").write_text("samtools 1.23\n")
        (self.work / "bam.flagstat").write_text("10 + 0 in total\n")
        (self.work / "cram.flagstat").write_text("10 + 0 in total\n" if matching else "9 + 0 in total\n")

    def test_worker_hooks_complete_and_restore_reports_without_source(self):
        self.worker_step("prepare")
        self.assertEqual((self.work / "finalization.action").read_text().strip(), "convert")
        self.worker_conversion_outputs()
        self.worker_step("authorize")
        self.assertEqual(records.load_record(self.manifest, self.entry)["state"], "cleanup_pending")
        for name in self.entry["published_file_sizes"]:
            Path(name).unlink()
        self.worker_step("complete")
        (self.work / "bam.flagstat").unlink()
        (self.work / "cram.flagstat").unlink()
        self.worker_step("prepare")
        self.assertEqual((self.work / "finalization.action").read_text().strip(), "complete")
        self.assertEqual((self.work / "bam.flagstat").read_text(), "10 + 0 in total\n")
        self.check("success")

    def test_worker_hooks_resume_partial_cleanup(self):
        self.worker_step("prepare")
        self.worker_conversion_outputs()
        self.worker_step("authorize")
        self.bam.unlink()
        self.worker_step("prepare")
        self.assertEqual((self.work / "finalization.action").read_text().strip(), "cleanup")
        self.worker_step("authorize")
        for name in self.entry["published_file_sizes"]:
            path = Path(name)
            if path.exists():
                path.unlink()
        self.worker_step("complete")
        self.check("success")

    def test_worker_hooks_reject_mismatching_flagstats(self):
        self.worker_step("prepare")
        self.worker_conversion_outputs(matching=False)
        with self.assertRaisesRegex(records.RecordError, "flagstats comparison"):
            self.worker_step("authorize")
        self.assertEqual(records.load_record(self.manifest, self.entry)["state"], "pending")
        self.assertTrue(self.bam.exists())

    def test_worker_hooks_record_retention_without_deletion_permission(self):
        self.worker_step("prepare", delete_source=False)
        self.worker_conversion_outputs()
        self.worker_step("authorize", delete_source=False)
        self.worker_step("complete", delete_source=False)
        self.assertEqual(records.load_record(self.manifest, self.entry)["state"], "complete")
        self.assertTrue(self.bam.exists())
        self.bam.unlink()
        self.check("unknown")

    def test_alignment_hooks_resume_partial_deletion(self):
        self.entry['stage'] = 'align-DNA'
        previous = Path.cwd()
        try:
            os.chdir(self.work)
            for step in ('prepare', 'authorize'):
                records.worker_record_step(self.manifest, self.entry, step, True, 'delete_alignment')
            self.assertEqual(records.load_record(self.manifest, self.entry)['state'], 'cleanup_pending')
            self.bam.unlink()
            self.check('success')
            for step in ('prepare', 'authorize'):
                records.worker_record_step(self.manifest, self.entry, step, True, 'delete_alignment')
            for name in self.entry['published_indexes'] + self.entry['published_checksums']:
                Path(name).unlink()
            records.worker_record_step(self.manifest, self.entry, 'complete', True, 'delete_alignment')
            self.assertEqual(records.load_record(self.manifest, self.entry)['state'], 'complete')
            self.assertFalse(self.cram.exists())
            self.check('success')
        finally:
            os.chdir(previous)

    def test_alignment_action_rejects_recalibrated_entry(self):
        with self.assertRaisesRegex(records.RecordError, 'Invalid finalization action'):
            records.worker_record_step(self.manifest, self.entry, 'prepare', True, 'delete_alignment')
        self.assertTrue(self.bam.exists())


if __name__ == "__main__":
    unittest.main()
