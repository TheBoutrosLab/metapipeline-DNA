"""Run the production status process with a mixed-success batch.

Usage: python3 test_status_process.py [nextflow launcher command ...]
No Slurm jobs are submitted; all child results are local fixtures.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[2]


# Keep the ordered integration scenario and its fixtures together.
# pylint: disable-next=too-many-locals
def main():
    """Run the production status process with mixed child results."""
    with tempfile.TemporaryDirectory(prefix="status-process-test-") as temporary:
        root = Path(temporary)
        inputs = []
        for identifier, code in [
            ("good-sample", 0), ("failed-sample", 1), ("unknown-sample", None)
        ]:
            work = root / "child" / "aa" / identifier
            records = work / "PIPELINEEXITSTATUS"
            records.mkdir(parents=True)
            (records / "expected_pipelines.json").write_text('["call-gSNP"]', encoding="utf-8")
            (records / "call-gSNP.0").touch()
            identity = {'schema_version': 1, 'run_id': "aa_" + identifier,
                            'workflow_identifier': identifier, 'patient': "P"}
            if code is not None:
                (work / "child_completion.json").write_text(json.dumps(dict(identity,
                    exit_code=code)), encoding="utf-8")
            manifest = work / "OUTPUTMANIFEST/output_manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps(dict(identity, publish_root=str(root / "published"),
                entries=[])), encoding="utf-8")
            inputs.append([str(work), "-1", identifier, str(manifest)])
        (root / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")

        # Exercise the process definition from main.nf without launching the
        # production entry workflow or duplicating that definition in fixtures.
        source = (REPO / "main.nf").read_text(encoding="utf-8")
        quote = source[source.index("def shellQuote("):source.index("// Log info here")]
        process = source[
            source.index("process check_process_status {"):source.index("\nworkflow {")]
        process = process.replace('${projectDir}/templates/check_workflow_status.py',
            str(REPO / 'templates/check_workflow_status.py'))
        process = process.replace("template 'status_check.sh'",
            "template '" + str(REPO / 'templates/status_check.sh') + "'")
        handoff = source[
            source.index("    // Only verified success"):source.index("    workflow.onComplete")]
        script = root / "test.nf"
        script.write_text('''nextflow.enable.dsl = 2
import groovy.json.JsonSlurper
params.log_output_dir = "${projectDir}/logs"
''' + "include { finalize_outputs } from '"
    + str(REPO / "module/finalize_outputs") + "'\n" + quote + process + '''
workflow {
    def inputs = new JsonSlurper().parse(new File("${projectDir}/inputs.json"))
    check_process_status(Channel.fromList(inputs))
''' + handoff + '''
    finalization_ready.toList().view { results ->
        assert results.size() == 1
        assert results[0][0] == 'good-sample'
        assert new JsonSlurper().parse(results[0][1]).workflow_identifier == 'good-sample'
        'Only the successful sample reached finalization_ready'
    }
    check_process_status.out.status.toList().view { results ->
        assert results.collectEntries { [(it[0]): it[1]] } == [
            'good-sample': 'success', 'failed-sample': 'failed', 'unknown-sample': 'unknown']
        assert results.every { new JsonSlurper().parse(it[2]).status == it[1] }
        'All sample statuses were reported without aborting the batch'
    }
    finalize_outputs.out.plans.toList().view { plans ->
        assert plans.size() == 2
        assert plans.every { it[0] == 'good-sample' }
        'Only the verified sample submitted finalization planning tasks'
    }
}
''', encoding="utf-8")
        config = root / 'nextflow.config'
        config.write_text(
            "includeConfig '" + str(REPO / 'test/test-output-manifest/nextflow.config') + "'\n"
            + "includeConfig '" + str(REPO / 'config/default.config') + "'\n"
            + 'params.final_output_dir = "${projectDir}/main_workflow"\n'
            + "params.finalization_queue = ''\n"
            + "params.finalization_cluster_options = ''\n"
            + "params.finalization_max_conversions = 1\n"
            + "params.finalization_max_alignment_deletions = 1\n", encoding="utf-8")
        result = subprocess.run((sys.argv[1:] or ["nextflow"]) + [
            "-C", str(config), "run", str(script), "-ansi-log", "false", "-w", str(root / "work"),
            "--finalization_executor", "local", "--finalization_cram_recalibrated_bams",
            "true"], cwd=root, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        reports = list(
            (root / "main_workflow/intermediate/workflow-status").rglob("workflow_status.json"))
        assert not (root / 'logs/workflow-status').exists()
        assert len(reports) == 3, reports
        assert {json.loads(p.read_text(encoding="utf-8"))["status"] for p in reports} == {
            "success", "failed", "unknown"}
        exit_codes = [
            p.read_text(encoding="utf-8").strip() for p in (root / "work").rglob(".exitcode")]
        assert exit_codes == ["0"] * 5, exit_codes
        assert len(
            list((root / 'main_workflow/intermediate/output-finalization-plans').rglob(
                'finalization_plan.json'))) == 2
        log_root = root / 'logs/process-log/finalize_outputs/plan_output_finalization'
        assert len(list(log_root.glob('*/log.command.sh'))) == 2
        print("Three status reports published; only the verified manifest reached "
            "finalization planning")


if __name__ == "__main__":
    main()
