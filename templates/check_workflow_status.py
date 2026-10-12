#!/usr/bin/env python3
"""Verify a nested workflow and report failures as data, without failing the batch."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


class Unverified(Exception):
    def __init__(self, reason, status="unknown"):
        super().__init__(reason)
        self.status = status


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise Unverified("Missing or unreadable evidence {}: {}".format(path, error))


def command(arguments):
    try:
        result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def verify_slurm(submission, attempts, interval):
    match = re.fullmatch(r"Submitted batch job (\d+)", submission.strip())
    if not match:
        raise Unverified("Unrecognized submission result: {}".format(submission))
    job_id = match.group(1)
    failures = 0
    while True:
        queue = command(["squeue", "--noheader", "--jobs", job_id, "--format=%i"])
        if queue is None:
            failures += 1
            if failures >= attempts:
                # Accounting can still establish completion if queue queries
                # fail; a queue error alone never establishes success.
                break
        elif queue.strip():
            failures = 0
        else:
            break
        time.sleep(interval)

    # Accounting may lag behind squeue. Require the allocation's own final row,
    # not a successful .batch/.extern step or an arbitrary sorted exit code.
    for attempt in range(attempts):
        accounting = command(["sacct", "--allocations", "--noheader", "--parsable2",
                              "--jobs", job_id, "--format=JobIDRaw,State,ExitCode"])
        rows = [] if accounting is None else [line.strip().split("|") for line in accounting.splitlines()]
        rows = [row for row in rows if len(row) == 3 and row[0].strip() == job_id]
        if len(rows) == 1:
            state, exit_code = rows[0][1].strip(), rows[0][2].strip()
            state = state.split()[0].rstrip('+') if state else ''
            if state == "COMPLETED" and exit_code == "0:0":
                return
            if state in {"FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL",
                         "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED"}:
                raise Unverified("Slurm job {} ended {} ({})".format(job_id, state, exit_code), "failed")
            if state == "COMPLETED" and re.fullmatch(r"\d+:\d+", exit_code):
                raise Unverified("Slurm job {} exited {}".format(job_id, exit_code), "failed")
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise Unverified("No unambiguous successful Slurm accounting record for job " + job_id)


def verify_pipeline_records(directory):
    # Collect available failures even if the inventory or another record is
    # missing/malformed. Incomplete evidence must not hide known failures.
    issues = []
    records = {}
    try:
        for path in directory.iterdir():
            if path.name == "expected_pipelines.json":
                continue
            try:
                match = re.fullmatch(r"(.+)\.(\d+)", path.name)
                if not match or not path.is_file():
                    issues.append("Unrecognized pipeline status record: " + str(path))
                    continue
                records.setdefault(match.group(1), []).append(int(match.group(2)))
            except OSError as error:
                issues.append("Could not read pipeline status record {}: {}".format(path, error))
    except OSError as error:
        issues.append("Could not read pipeline status directory {}: {}".format(directory, error))
    failed = sorted(name for name, codes in records.items() if any(code != 0 for code in codes))
    if failed:
        issues.insert(0, "Downstream pipelines failed: " + ", ".join(failed))
    try:
        expected = read_json(directory / "expected_pipelines.json")
        if not isinstance(expected, list) or not all(isinstance(name, str) and name for name in expected):
            raise Unverified("Invalid expected pipeline inventory")
        missing = sorted(set(expected) - set(records))
        if missing:
            issues.append("Missing downstream status records: " + ", ".join(missing))
    except Unverified as error:
        issues.append(str(error))
    if issues:
        raise Unverified("; ".join(issues), "failed" if failed else "unknown")


def verify_manifest(manifest, receipt, identifier):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise Unverified("Invalid manifest schema")
    for key in ("run_id", "workflow_identifier", "patient"):
        if not isinstance(receipt.get(key), str) or not receipt[key] or manifest.get(key) != receipt[key]:
            raise Unverified("Manifest/completion identity mismatch: " + key)
    if manifest["workflow_identifier"] != identifier:
        raise Unverified("Manifest belongs to another patient/sample workflow")
    root = Path(manifest.get("publish_root", ""))
    entries = manifest.get("entries")
    if not root.is_absolute() or not isinstance(entries, list):
        raise Unverified("Invalid manifest output root or entries")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("generated") is not True:
            raise Unverified("Manifest contains an entry without generated-output provenance")
        if any(entry.get(key) != manifest[key] for key in ("schema_version", "run_id", "workflow_identifier", "patient")):
            raise Unverified("Manifest entry belongs to another workflow or schema")
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or entry_id != entry.get("published_bam") or entry_id in seen:
            raise Unverified("Missing, inconsistent or duplicate manifest entry ID")
        seen.add(entry_id)
        sizes = entry.get("published_file_sizes")
        indexes, checksums = entry.get("published_indexes"), entry.get("published_checksums")
        if not isinstance(indexes, list) or not isinstance(checksums, list) or not isinstance(sizes, dict):
            raise Unverified("Missing publication metadata for " + entry_id)
        paths = [entry_id] + indexes + checksums
        if not all(isinstance(path, str) for path in paths) or set(paths) != set(sizes):
            raise Unverified("Incomplete publication size inventory for " + entry_id)
        for name, expected_size in sizes.items():
            path = Path(name)
            if not path.is_absolute() or type(expected_size) is not int or expected_size < 0:
                raise Unverified("Invalid published-file metadata: " + name)
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                raise Unverified("Published file is outside the output root: " + name)
            if not path.is_file() or path.stat().st_size != expected_size:
                raise Unverified("Published file is missing or incomplete: " + name)


def check(work_dir, submission, identifier, manifest_path, output_dir, attempts=10, interval=3):
    report = {"schema_version": 1, "workflow_identifier": identifier, "status": "unknown"}
    pipeline_records_checked = False
    try:
        # This is our task-local handoff, not the published/source manifest.
        destination = output_dir / "output_manifest.json"
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        if submission != "-1":
            verify_slurm(submission, attempts, interval)
        receipt = read_json(work_dir / "child_completion.json")
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or type(receipt.get("exit_code")) is not int:
            raise Unverified("Invalid child completion receipt")
        expected_run = "{}_{}".format(work_dir.parent.name, work_dir.name)
        if receipt.get("run_id") != expected_run or receipt.get("workflow_identifier") != identifier:
            raise Unverified("Child completion receipt belongs to another invocation")
        report["run_id"] = expected_run
        if receipt["exit_code"] != 0:
            raise Unverified("Child Nextflow exited {}".format(receipt["exit_code"]), "failed")
        pipeline_records_checked = True
        verify_pipeline_records(work_dir / "PIPELINEEXITSTATUS")
        manifest = read_json(manifest_path)
        verify_manifest(manifest, receipt, identifier)
        temporary = output_dir / "output_manifest.json.tmp"
        shutil.copyfile(str(manifest_path), str(temporary))
        if read_json(temporary) != manifest:
            raise Unverified("Manifest changed while being staged")
        temporary.replace(destination)
        report.update(status="success", reason="Child, downstream statuses and published outputs verified")
    except Unverified as error:
        report.update(status=error.status, reason=str(error))
    except Exception as error:
        report.update(status="unknown", reason="Status verification error: {}".format(error))

    # Slurm/child failures block the handoff, but must not skip the downstream
    # diagnostics that were printed by the original status-check script.
    if not pipeline_records_checked:
        try:
            verify_pipeline_records(work_dir / "PIPELINEEXITSTATUS")
        except Unverified as error:
            report["reason"] += "; " + str(error)
            if error.status == "failed":
                report["status"] = "failed"
        except Exception as error:
            report["reason"] += "; Downstream status verification error: {}".format(error)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.work_dir, args.submission, args.identifier, args.manifest, Path.cwd())
    Path("workflow_status.json").write_text(json.dumps(report, indent=2) + "\n")
    Path("workflow_status.txt").write_text(report["status"] + "\n")
    print("{}: {} - {}".format(args.identifier, report["status"], report["reason"]))
    return 0


if __name__ == "__main__":
    main()
