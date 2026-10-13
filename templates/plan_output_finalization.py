#!/usr/bin/env python3
"""Plan pending finalization work using metadata; never convert or delete files."""
import argparse
import json
from pathlib import Path

from check_workflow_status import verify_manifest
from finalization_records import RecordError, load_record, new_record, record_path, recovery_action


def plan_finalization(manifest, identifier, cram=False, delete_alignment=False, delete_recalibrated=False):
    # Recheck publication/record metadata on the worker node. A prior status
    # check does not authorize changes to files that have since changed.
    verify_manifest(manifest, manifest, identifier)
    conversions, cleanup, completed, skipped = [], [], [], []
    for entry in sorted(manifest["entries"], key=lambda entry: entry["entry_id"]):
        stage = entry.get("stage")
        selected = ((stage == "align-DNA" and delete_alignment)
                    or (stage == "recalibrate-BAM" and (cram or delete_recalibrated)))
        if not selected:
            skipped.append(entry["entry_id"])
            continue
        action = "delete_alignment" if stage == "align-DNA" else "cram"
        delete_source = delete_alignment if stage == "align-DNA" else delete_recalibrated
        record = load_record(manifest, entry)
        recorded_state = None if record is None else record["state"]
        if record is None:
            # Validate a possible new record without creating it. The execution
            # worker must recheck metadata and save intent before cleanup.
            record = new_record(manifest, entry, action, delete_source=delete_source)
        if record["action"] != action or bool(record["deletions"]) != delete_source:
            raise RecordError("Requested action/deletion policy differs from saved intent: " + entry["entry_id"])
        next_action = recovery_action(manifest, entry, record)
        if next_action == "complete":
            completed.append(entry["entry_id"])
            continue
        request = {
            "entry_id": entry["entry_id"], "entry": entry, "action": action,
            "record_path": str(record_path(manifest, entry)), "recorded_state": recorded_state,
            "delete_source": delete_source,
        }
        if next_action == "convert":
            if not cram:
                raise RecordError("BAM deletion requires a validated CRAM or enabled conversion: " + entry["entry_id"])
            for key in ("reference_fasta", "reference_fasta_fai"):
                reference = Path(entry.get(key, ""))
                if not reference.is_absolute() or not reference.is_file():
                    raise RecordError("Missing accessible conversion reference {}: {}".format(key, reference))
        if action == "cram":
            # The same worker resumes validated/partially cleaned entries,
            # skipping conversion and finishing only the outstanding metadata
            # and deletions. Separate cleanup must not delete these BAMs again.
            conversions.append(request)
        else:
            cleanup.append(dict(request, deletion_paths=list(record["deletions"])))
    return {
        "schema_version": 1,
        "mode": "plan_only",
        "run_id": manifest["run_id"],
        "workflow_identifier": identifier,
        "patient": manifest["patient"],
        "options": {"cram_recalibrated_bams": cram, "delete_alignment_bams": delete_alignment,
                    "delete_recalibrated_bams": delete_recalibrated},
        "conversions": conversions,
        "cleanup": {
            # All cleanup for this invocation must wait for every required
            # conversion. These are requests, not permission to unlink files.
            "wait_for_entry_ids": [item["entry_id"] for item in conversions],
            "entries": cleanup,
        },
        "completed": completed,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--cram-recalibrated-bams", action="store_true")
    parser.add_argument("--delete-alignment-bams", action="store_true")
    parser.add_argument("--delete-recalibrated-bams", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    plan = plan_finalization(manifest, args.identifier, args.cram_recalibrated_bams,
                             args.delete_alignment_bams, args.delete_recalibrated_bams)
    temporary = Path("finalization_plan.json.tmp")
    temporary.write_text(json.dumps(plan, indent=2) + "\n")
    temporary.replace("finalization_plan.json")
    print("{}: planned {} conversions, {} cleanup entries; {} already complete, {} skipped".format(
        args.identifier, len(plan["conversions"]), len(plan["cleanup"]["entries"]),
        len(plan["completed"]), len(plan["skipped"])))


if __name__ == "__main__":
    main()
