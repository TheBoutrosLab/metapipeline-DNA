"""Request planning must be read-only and honor saved intent for independent arms."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_finalization_records as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'templates'))
import finalization_records as records
from plan_output_finalization import plan_finalization


class FinalizationPlanTest(unittest.TestCase):
    # Share fixture setup/helpers, without inheriting and rerunning its tests.
    setUp = fixtures.FinalizationRecordsTest.setUp
    start = fixtures.FinalizationRecordsTest.start
    convert = fixtures.FinalizationRecordsTest.convert
    begin_cleanup = fixtures.FinalizationRecordsTest.begin_cleanup
    finish = fixtures.FinalizationRecordsTest.finish

    def plan(self, **options):
        return plan_finalization(self.manifest, 'P', **options)

    def add_entry(self, name, stage='recalibrate-BAM', sample='S'):
        path = self.published / name
        path.write_text('fixture')
        entry = dict(self.entry, entry_id=str(path), published_bam=str(path), sample=sample,
                     stage=stage, published_indexes=[], published_checksums=[],
                     published_file_sizes={str(path): 7})
        self.manifest['entries'].append(entry)
        return entry

    def test_all_disabled_has_no_requests(self):
        plan = self.plan()
        self.assertEqual(plan['conversions'], [])
        self.assertEqual(plan['cleanup']['entries'], [])
        self.assertEqual(plan['skipped'], [self.entry['entry_id']])

    def test_empty_manifest(self):
        self.manifest['entries'] = []
        plan = self.plan(cram=True, delete_alignment=True)
        self.assertEqual(plan['conversions'], [])
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_conversion_preserves_bam_by_default_and_does_not_create_records(self):
        plan = self.plan(cram=True)
        self.assertEqual(plan['mode'], 'plan_only')
        request = plan['conversions'][0]
        self.assertEqual(request['entry_id'], self.entry['entry_id'])
        self.assertIs(request['delete_source'], False)
        self.assertEqual(plan['cleanup']['entries'], [])
        self.assertTrue(self.bam.exists())
        self.assertFalse(records.record_path(self.manifest, self.entry).exists())

    def test_alignment_cleanup_is_independent_of_multiple_conversions(self):
        second = self.add_entry('paired-second-normal.bam')
        alignment = self.add_entry('alignment.bam', stage='align-DNA')
        plan = self.plan(cram=True, delete_alignment=True, delete_recalibrated=True)
        expected = {self.entry['entry_id'], second['entry_id']}
        self.assertEqual({r['entry_id'] for r in plan['conversions']}, expected)
        self.assertNotIn('wait_for_entry_ids', plan['cleanup'])
        self.assertEqual({r['entry_id'] for r in plan['cleanup']['entries']}, {alignment['entry_id']})
        self.assertEqual(self.entry['sample'], second['sample'])

    def test_alignment_only_plans_no_conversion(self):
        alignment = self.add_entry('aligned.bam', stage='align-DNA')
        plan = self.plan(delete_alignment=True)
        self.assertEqual(plan['conversions'], [])
        self.assertNotIn('wait_for_entry_ids', plan['cleanup'])
        self.assertEqual(plan['cleanup']['entries'][0]['entry_id'], alignment['entry_id'])
        self.assertEqual(plan['skipped'], [self.entry['entry_id']])

    def test_unconverted_recalibrated_bam_cannot_be_deleted_without_conversion(self):
        with self.assertRaisesRegex(records.RecordError, 'requires a validated CRAM'):
            self.plan(delete_recalibrated=True)

    def test_pending_conversion_is_planned_again(self):
        self.start(delete_source=True)
        plan = self.plan(cram=True, delete_recalibrated=True)
        self.assertEqual(plan['conversions'][0]['recorded_state'], 'pending')

    def test_validated_cram_needs_only_cleanup(self):
        self.convert(self.start())
        plan = self.plan(delete_recalibrated=True)
        self.assertEqual(plan['conversions'][0]['recorded_state'], 'validated')
        self.assertNotIn('wait_for_entry_ids', plan['cleanup'])
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_partial_cleanup_does_not_reconvert(self):
        self.begin_cleanup(self.convert(self.start()))
        self.bam.unlink()
        plan = self.plan(cram=True, delete_recalibrated=True)
        self.assertEqual(plan['conversions'][0]['recorded_state'], 'cleanup_pending')
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_completed_entries_have_no_requests(self):
        self.finish(self.begin_cleanup(self.convert(self.start())))
        plan = self.plan(cram=True, delete_recalibrated=True)
        self.assertEqual(plan['completed'], [self.entry['entry_id']])
        self.assertEqual(plan['conversions'], [])
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_changed_deletion_policy_is_not_silently_applied(self):
        self.start(delete_source=False)
        with self.assertRaisesRegex(records.RecordError, 'differs from saved intent'):
            self.plan(cram=True, delete_recalibrated=True)

    def test_disabled_action_does_not_resume_saved_deletion(self):
        self.begin_cleanup(self.convert(self.start()))
        plan = self.plan()
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_changed_file_blocks_planning(self):
        self.bam.write_text('changed size')
        with self.assertRaisesRegex(Exception, 'missing or incomplete'):
            self.plan(cram=True)

    def test_wrong_workflow_manifest_is_rejected(self):
        with self.assertRaisesRegex(Exception, 'another patient/sample'):
            plan_finalization(self.manifest, 'different-workflow', cram=True)

    def test_missing_reference_blocks_conversion_request(self):
        self.fai.unlink()
        with self.assertRaisesRegex(records.RecordError, 'Missing accessible conversion reference'):
            self.plan(cram=True)

    def test_conversion_reference_failure_does_not_block_alignment_plan(self):
        alignment = self.add_entry('aligned.bam', stage='align-DNA')
        self.fai.unlink()
        with self.assertRaisesRegex(records.RecordError, 'Missing accessible conversion reference'):
            self.plan(cram=True)
        plan = self.plan(delete_alignment=True)
        self.assertEqual([entry['entry_id'] for entry in plan['cleanup']['entries']], [alignment['entry_id']])
        self.assertEqual(plan['conversions'], [])

    def test_alignment_metadata_failure_does_not_block_conversion_plan(self):
        alignment = self.add_entry('aligned.bam', stage='align-DNA')
        Path(alignment['published_bam']).unlink()
        with self.assertRaisesRegex(Exception, 'missing or incomplete'):
            self.plan(delete_alignment=True)
        plan = self.plan(cram=True)
        self.assertEqual([entry['entry_id'] for entry in plan['conversions']], [self.entry['entry_id']])
        self.assertEqual(plan['cleanup']['entries'], [])

    def test_plan_has_only_metadata_reads_and_never_saves_records(self):
        self.convert(self.start(delete_source=False))
        record_file = records.record_path(self.manifest, self.entry)
        before = record_file.read_bytes()
        original_open = Path.open

        def json_only(path, *args, **kwargs):
            self.assertEqual(path.suffix, '.json')
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', json_only), patch.object(records, 'save_record', side_effect=AssertionError('write')):
            plan = self.plan(cram=True)
        self.assertEqual(record_file.read_bytes(), before)
        self.assertEqual(plan['conversions'][0]['recorded_state'], 'validated')
        self.assertEqual(plan['cleanup']['entries'], [])


if __name__ == '__main__':
    unittest.main()
