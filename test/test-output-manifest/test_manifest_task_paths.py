"""Exercise wrapper output declarations using tiny stand-in scripts.

Usage: python3 test_manifest_task_paths.py [nextflow launcher command ...]
"""
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    """Verify task context paths using small stand-in child outputs."""
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='manifest-context-') as temp:
        root = Path(temp)
        definitions = []
        for name, outputs in [
            ('align_DNA/call_align_DNA', 'mkdir -p align-DNA-1/S'),
            ('recalibrate_BAM/run_recalibrate_BAM',
             'mkdir -p recalibrate-BAM-1/S/GATK-1/output '
             'recalibrate-BAM-1/S/GATK-1/QC/contamination_qc/run_CalculateContamination_GATK '
             'recalibrate-BAM-1/S/log-recalibrate-BAM-1/nextflow-log\n'
             'echo {} > recalibrate-BAM-1/S/log-recalibrate-BAM-1/nextflow-log/params.json')
        ]:
            source = (repo / ('module/' + name + '.nf')).read_text(encoding="utf-8")
            source = source[source.index('process '):]
            start = source.index('    """')
            source = source[:start]+'    """\n'+outputs+'\n    """\n}\n'
            definitions.append(source)
        (root / 'input.yaml').write_text('{}', encoding="utf-8")
        script = root / 'test.nf'
        script.write_text('''nextflow.enable.dsl = 2
    params.align_DNA = [subworkflow_cpus:1, metapipeline_arg_map:[:]]
    params.recalibrate_BAM = [subworkflow_cpus:1, metapipeline_arg_map:[:]]
    params.output_dir = "${projectDir}/published"
    params.metapipeline_log_output_dir = "${projectDir}/logs"
    params.task_hash = 'aa_hash'
    params.sample_mode = 'paired'
    params.patient = 'P'
    def combine_input_with_params(Map p, File input = null) { '' }
    def generate_weblog_args() { '' }
    '''+''.join(definitions)+'''
    workflow {
        call_align_DNA(Channel.of(['P','S','normal',file("${projectDir}/input.yaml")]))
        run_recalibrate_BAM(Channel.of([[], 'S', [normal:['S']],file("${projectDir}/input.yaml"),'complete']))
        call_align_DNA.out.align_dna_output_directory.view { sample, output, context ->
            assert context.task_work_dir == output.parent.parent.toString()
            assert context.run_id == 'aa_hash'
            assert context.workflow_identifier == 'P'
            "Alignment context uses ${context.task_work_dir}"
        }
        run_recalibrate_BAM.out.identify_recalibrate_bam_out.view { states, output, qc, context, resolved ->
            assert context.task_work_dir == output.parent.parent.parent.parent.toString()
            assert context.workflow_identifier == 'P'
            "Recalibration context uses ${context.task_work_dir}"
        }
    }
    ''', encoding="utf-8")
        command = (sys.argv[1:] or ['nextflow']) + ['-C',
            str(repo / 'test/test-output-manifest/nextflow.config'),'run',str(script),'-ansi-log',
            'false']
        result = subprocess.run(command, cwd=root, check=False)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
