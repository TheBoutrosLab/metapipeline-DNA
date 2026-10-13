"""Small, persistent finalization records. This module never reads BAM/CRAM contents.

Finalization runs for the same entry are assumed not to overlap. Workers persist
cleanup intent BEFORE unlinking anything and record completion afterwards.
These helpers record validation performed by the worker; they do not perform
conversion or validation.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile


class RecordError(ValueError):
    pass


STATES = ("pending", "validated", "cleanup_pending", "complete")
IDENTITY_KEYS = ("schema_version", "run_id", "workflow_identifier", "patient", "publish_root")


def record_path(manifest, entry):
    """Locate a record beside the retained manifest, independent of task workDir."""
    root = Path(manifest["publish_root"])
    if not root.is_absolute():
        raise RecordError("Finalization requires an absolute publication root")
    base = root.parent / "intermediate" / "output-manifests"
    directory = base / manifest["workflow_identifier"] / manifest["run_id"] / "finalization"
    try:
        directory.resolve().relative_to(base.resolve())
    except ValueError:
        raise RecordError("Finalization record directory escapes the manifest directory")
    # Hash only the path string, never the large file itself.
    key = hashlib.sha256(entry["entry_id"].encode("utf-8")).hexdigest()
    return directory / (key + ".json")


def _published_path(manifest, name):
    path = Path(name)
    if not path.is_absolute():
        raise RecordError("Finalization requires an absolute file path: " + name)
    try:
        path.resolve().relative_to(Path(manifest["publish_root"]).resolve())
    except ValueError:
        raise RecordError("Finalization file is outside the publication root: " + name)
    if path.is_symlink():
        raise RecordError("Finalization does not operate on file symlinks: " + name)
    return path


def _metadata(path):
    if not path.is_file():
        raise RecordError("Required finalization file is missing: " + str(path))
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _verify_metadata(path, expected):
    if (not isinstance(expected, dict) or set(expected) != {"size", "mtime_ns"}
            or any(type(value) is not int for value in expected.values()) or expected["size"] < 0):
        raise RecordError("Invalid recorded file metadata: " + str(path))
    if _metadata(path) != expected:
        raise RecordError("File changed since finalization was recorded: " + str(path))


def load_record(manifest, entry):
    path = record_path(manifest, entry)
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise RecordError("Finalization record must be a JSON object: " + str(path))
        return record
    except FileNotFoundError:
        if path.is_symlink():
            raise RecordError("Broken finalization record symlink: " + str(path))
        return None
    except (OSError, ValueError) as error:
        raise RecordError("Cannot read finalization record {}: {}".format(path, error))


def new_record(manifest, entry, action, delete_source=False):
    """Prepare a pending record while every source file is still present."""
    if load_record(manifest, entry) is not None:
        raise RecordError("Finalization record already exists; resume it instead")
    source_files = {}
    for name, size in entry["published_file_sizes"].items():
        metadata = _metadata(_published_path(manifest, name))
        if metadata["size"] != size:
            raise RecordError("Source size differs from manifest: " + name)
        source_files[name] = metadata
    record = {
        "schema_version": 1,
        "manifest_identity": {key: manifest[key] for key in IDENTITY_KEYS},
        "source_entry": deepcopy(entry),
        "source_files": source_files,
        "action": action,
        "state": "pending",
        "deletions": {name: "pending" for name in source_files}
            if delete_source or action == "delete_alignment" else {},
        "conversion": None,
    }
    recovery_action(manifest, entry, record)
    return record


def _verify_conversion(manifest, entry, conversion):
    if not isinstance(conversion, dict):
        raise RecordError("Missing validated CRAM conversion record")
    expected_paths = {"cram": str(Path(entry["published_bam"]).with_suffix(".cram"))}
    expected_paths["crai"] = expected_paths["cram"] + ".crai"
    for kind, expected_path in expected_paths.items():
        output = conversion.get(kind, {})
        if output.get("path") != expected_path:
            raise RecordError("Unexpected replacement {} path".format(kind))
        _verify_metadata(_published_path(manifest, expected_path), output.get("metadata"))
    for key in ("reference_fasta", "reference_fasta_fai"):
        reference = conversion.get(key, {})
        if not entry.get(key) or reference.get("path") != entry[key]:
            raise RecordError("Conversion reference differs from source manifest: " + key)
        _verify_metadata(Path(reference["path"]), reference.get("metadata"))
    tool = conversion.get("tool", {})
    if tool.get("name") != "samtools" or not isinstance(tool.get("version"), str) or not tool["version"].strip():
        raise RecordError("Missing conversion tool/version")
    settings = conversion.get("settings", {})
    if not isinstance(settings, dict) or settings != {"store_md": True, "store_nm": True}:
        raise RecordError("Missing CRAM conversion settings")
    validation = conversion.get("validation", {})
    # Flagstats compare counts/flags, not all record contents. Retain the small
    # reports so a resumed task can emit them without scanning the CRAM again.
    if (not isinstance(validation, dict) or validation.get("method") != "flagstat"
            or not isinstance(validation.get("bam_flagstat"), str)
            or not validation["bam_flagstat"].strip()
            or validation["bam_flagstat"] != validation.get("cram_flagstat")):
        raise RecordError("Missing or unsuccessful flagstats comparison")


def recovery_action(manifest, entry, record):
    """Validate identity and metadata; return convert, cleanup, or complete.

    An absent pending deletion is allowed only AFTER cleanup intent was saved.
    This handles a crash between unlinking a file and recording that unlink.
    """
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("manifest_identity") != {key: manifest[key] for key in IDENTITY_KEYS}
            or record.get("source_entry") != entry):
        raise RecordError("Finalization record belongs to a different manifest entry or invocation")
    action, state = record.get("action"), record.get("state")
    if state not in STATES or (action, entry.get("stage")) not in {
            ("cram", "recalibrate-BAM"), ("delete_alignment", "align-DNA")} or entry.get("generated") is not True:
        raise RecordError("Invalid finalization action, stage or state")
    sizes = entry.get("published_file_sizes", {})
    source_files, deletions = record.get("source_files"), record.get("deletions")
    expected_paths = [entry["published_bam"]] + entry["published_indexes"] + entry["published_checksums"]
    if (not sizes or set(sizes) != set(expected_paths) or entry["entry_id"] != entry["published_bam"]
            or any(type(size) is not int or size < 0 for size in sizes.values())
            or not isinstance(source_files, dict) or set(source_files) != set(sizes)
            or not isinstance(deletions, dict) or not set(deletions).issubset(sizes)
            or any(value not in ("pending", "deleted") for value in deletions.values())):
        raise RecordError("Invalid finalization source/deletion inventory")
    if action == "delete_alignment" and (set(deletions) != set(sizes) or state == "validated"
                                         or record.get("conversion") is not None):
        raise RecordError("Invalid alignment deletion record")
    if action == "cram":
        if state == "pending":
            if record.get("conversion") is not None:
                raise RecordError("Pending conversion cannot claim validated outputs")
        else:
            _verify_conversion(manifest, entry, record.get("conversion"))
    for name, size in sizes.items():
        path = _published_path(manifest, name)
        metadata = source_files[name]
        if (not isinstance(metadata, dict) or set(metadata) != {"size", "mtime_ns"}
                or any(type(value) is not int for value in metadata.values()) or metadata["size"] != size):
            raise RecordError("Recorded source size differs from manifest: " + name)
        deleted = deletions.get(name) == "deleted"
        cleanup_started = state in ("cleanup_pending", "complete")
        if deleted and not cleanup_started:
            raise RecordError("Deletion recorded before cleanup was authorized: " + name)
        if state == "complete" and name in deletions and not deleted:
            raise RecordError("Complete record contains unfinished deletions")
        if path.exists():
            if deleted:
                raise RecordError("Previously deleted source file has reappeared: " + name)
            _verify_metadata(path, metadata)
        elif not (cleanup_started and name in deletions):
            raise RecordError("Source file is missing without recorded cleanup intent: " + name)
    if state == "complete":
        return "complete"
    if action == "cram" and state == "pending":
        return "convert"
    return "cleanup"


def record_conversion(manifest, entry, record, tool_version, settings, validation):
    """Capture already-promoted outputs after the worker validates the CRAM."""
    recovery_action(manifest, entry, record)
    if record["action"] != "cram" or record["state"] != "pending":
        raise RecordError("Only a pending conversion can be marked validated")
    updated = deepcopy(record)
    cram = str(Path(entry["published_bam"]).with_suffix(".cram"))
    paths = {"cram": cram, "crai": cram + ".crai",
             "reference_fasta": entry["reference_fasta"], "reference_fasta_fai": entry["reference_fasta_fai"]}
    updated["conversion"] = {kind: {"path": name, "metadata": _metadata(Path(name))}
                             for kind, name in paths.items()}
    updated["conversion"].update(tool={"name": "samtools", "version": tool_version},
                                 settings=deepcopy(settings), validation=deepcopy(validation))
    updated["state"] = "validated"
    recovery_action(manifest, entry, updated)
    return updated


def begin_cleanup(manifest, entry, record):
    """Save the returned intent record before deleting any source files."""
    if recovery_action(manifest, entry, record) != "cleanup":
        raise RecordError("Entry is not ready for cleanup")
    updated = deepcopy(record)
    updated["state"] = "cleanup_pending" if updated["deletions"] else "complete"
    return updated


def record_deletion(manifest, entry, record, name):
    """Acknowledge an already-absent file, including recovery after interruption."""
    recovery_action(manifest, entry, record)
    if record["state"] != "cleanup_pending" or name not in record["deletions"]:
        raise RecordError("File is not scheduled for cleanup: " + name)
    if _published_path(manifest, name).exists():
        raise RecordError("Cannot record deletion of an existing file: " + name)
    updated = deepcopy(record)
    updated["deletions"][name] = "deleted"
    if all(state == "deleted" for state in updated["deletions"].values()):
        updated["state"] = "complete"
    return updated


def save_record(manifest, entry, record):
    """Atomically persist a small record; runs for this entry must not overlap."""
    recovery_action(manifest, entry, record)
    previous = load_record(manifest, entry)
    if previous is None and record["state"] != "pending":
        raise RecordError("Save a pending record before starting finalization")
    if previous is not None:
        # Refuse stale updates that would undo recorded progress or change intent.
        if (previous.get("manifest_identity") != record["manifest_identity"]
                or previous.get("source_entry") != record["source_entry"]
                or previous.get("action") != record["action"]
                or previous.get("source_files") != record["source_files"]
                or set(previous.get("deletions", {})) != set(record["deletions"])
                or previous.get("state") not in STATES
                or STATES.index(previous["state"]) > STATES.index(record["state"])
                or any(state == "deleted" and record["deletions"].get(name) != "deleted"
                       for name, state in previous.get("deletions", {}).items())
                or (previous.get("conversion") is not None and previous["conversion"] != record["conversion"])):
            raise RecordError("Finalization update changes identity or reverses recorded progress")
        allowed = {
            "pending": {"pending", "validated"} if record["action"] == "cram" else {"pending", "cleanup_pending"},
            "validated": {"validated", "cleanup_pending"} if record["deletions"] else {"validated", "complete"},
            "cleanup_pending": {"cleanup_pending", "complete"},
            "complete": {"complete"},
        }
        if record["state"] not in allowed[previous["state"]]:
            raise RecordError("Invalid finalization state transition")
        if previous["state"] not in ("cleanup_pending", "complete"):
            if any(not Path(name).exists() for name in record["source_files"]):
                raise RecordError("Cleanup intent must be persisted before deleting source files")
    destination = record_path(manifest, entry)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=str(destination.parent), prefix=".record-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(destination))
        directory_fd = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def verify_publication(manifest, entry):
    """Check original files or a matching finalization record, using metadata only."""
    record = load_record(manifest, entry)
    if record is not None:
        return recovery_action(manifest, entry, record)
    for name, size in entry["published_file_sizes"].items():
        path = Path(name)
        if not path.is_file() or path.stat().st_size != size:
            raise RecordError("Published file is missing or incomplete: " + name)
    return "unprocessed"


def worker_record_step(manifest, entry, step, delete_source=False, record_action="cram"):
    """Metadata hooks for finalization tasks; never convert or unlink files.

    The host-side hooks assume runs for this entry do not overlap.
    Conversion/flagstats run in the samtools container; shell cleanup follows
    persisted intent.
    """
    record = load_record(manifest, entry)
    if record is None:
        if step != "prepare":
            raise RecordError("Finalization has no saved intent")
        record = new_record(manifest, entry, record_action, delete_source)
        save_record(manifest, entry, record)
    if record["action"] != record_action or bool(record["deletions"]) != delete_source:
        raise RecordError("Finalization policy differs from saved intent")
    action = recovery_action(manifest, entry, record)
    if step == "prepare":
        if record_action == "cram" and action != "convert":
            validation = record["conversion"]["validation"]
            Path("bam.flagstat").write_text(validation["bam_flagstat"])
            Path("cram.flagstat").write_text(validation["cram_flagstat"])
        Path("finalization.action").write_text(action + "\n")
    elif step == "authorize":
        if action == "convert":
            record = record_conversion(
                manifest, entry, record, Path("samtools.version").read_text().splitlines()[0],
                {"store_md": True, "store_nm": True},
                {"method": "flagstat", "bam_flagstat": Path("bam.flagstat").read_text(),
                 "cram_flagstat": Path("cram.flagstat").read_text()})
            save_record(manifest, entry, record)
        if record["state"] == "validated" or (record_action == "delete_alignment" and record["state"] == "pending"):
            record = begin_cleanup(manifest, entry, record)
            save_record(manifest, entry, record)
    elif step == "complete":
        for name, state in record["deletions"].items():
            if state != "deleted":
                record = record_deletion(manifest, entry, record, name)
                save_record(manifest, entry, record)
        if record["state"] != "complete":
            raise RecordError("Finalization cleanup is incomplete")
    else:
        raise RecordError("Unknown record step: " + step)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Update finalization metadata; do not convert or delete files")
    parser.add_argument("step", choices=("prepare", "authorize", "complete"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--entry-id", required=True)
    parser.add_argument("--delete-source", action="store_true")
    parser.add_argument("--action", choices=("cram", "delete_alignment"), default="cram")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    entry, = [entry for entry in manifest["entries"] if entry["entry_id"] == args.entry_id]
    worker_record_step(manifest, entry, args.step, args.delete_source, args.action)


if __name__ == "__main__":
    main()
