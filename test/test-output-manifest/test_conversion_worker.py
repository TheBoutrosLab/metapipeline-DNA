"""Test simple container-process commands locally with tiny real BAMs.

Set FINALIZATION_TEST_SAMTOOLS to an executable; optionally pass a Nextflow launcher.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'templates'))
import finalization_records as records
from check_workflow_status import verify_manifest


def main():
    samtools = os.environ.get('FINALIZATION_TEST_SAMTOOLS') or shutil.which('samtools')
    if not samtools:
        raise SystemExit('Set FINALIZATION_TEST_SAMTOOLS to a samtools executable')
    with tempfile.TemporaryDirectory(prefix='simple-conversion-') as temporary:
        root = Path(temporary)
        output = root / "output with 'quotes"
        output.mkdir()
        reference = root / 'reference.fa'
        reference.write_text('>chr1\n' + 'ACGT' * 25 + '\n')
        subprocess.run([samtools, 'faidx', str(reference)], check=True)
        sam = root / 'input.sam'
        sam.write_text('@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:100\n'
                       'r1\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\tNM:i:0\tMD:Z:4\n'
                       'r2\t4\t*\t0\t0\t*\t*\t0\t0\tTGCA\tJJJJ\n')
        stream_failures = ('hashfail', 'bamstatsfail', 'cramstatsfail', 'writefail')
        for name in ('good', 'mismatch', 'keep', 'interrupted', 'recordfail', 'mismatch_good', 'alignment_interrupted',
                     'planning_failure', 'alignment_plan_failure') + stream_failures:
            bam = output / (name + '.bam')
            subprocess.run([samtools, 'view', '--no-PG', '-b', '-o', str(bam), str(sam)], check=True)
            subprocess.run([samtools, 'index', str(bam)], check=True)
            checksum = Path(str(bam) + '.sha512')
            checksum.write_text('fixture checksum\n')
            paths = [bam, Path(str(bam) + '.bai'), checksum]
            identity = dict(schema_version=1, run_id='aa_child', workflow_identifier=name, patient='P')
            entry = dict(identity, entry_id=str(bam), published_bam=str(bam), generated=True,
                         stage='recalibrate-BAM', sample=name, aligner='BWA-MEM2',
                         reference_fasta=str(reference), reference_fasta_fai=str(reference) + '.fai',
                         published_indexes=[str(paths[1])], published_checksums=[str(checksum)],
                         published_file_sizes={str(p): p.stat().st_size for p in paths})
            (root / (name + '.json')).write_text(json.dumps(dict(identity, publish_root=str(output), entries=[entry])))
        # Alignment deletion must proceed even if one of two conversions fails.
        mismatch = json.loads((root / 'mismatch.json').read_text())
        second = json.loads((root / 'mismatch_good.json').read_text())['entries'][0]
        second['workflow_identifier'] = 'mismatch'
        mismatch['entries'].append(second)
        (root / 'mismatch.json').write_text(json.dumps(mismatch))
        alignment_workflows = ('good', 'mismatch', 'interrupted', 'recordfail', 'alignment_only', 'alignment_interrupted',
                              'planning_failure', 'alignment_plan_failure')
        for name in alignment_workflows:
            manifest_path = root / (name + '.json')
            identity = dict(schema_version=1, run_id='aa_child', workflow_identifier=name, patient='P')
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(identity, publish_root=str(output), entries=[])
            bam = output / (name + '.aligned.bam')
            paths = [bam, Path(str(bam) + '.bai'), Path(str(bam) + '.sha512')]
            for path in paths:
                path.write_text('alignment fixture')
            manifest['entries'].append(dict(identity, entry_id=str(bam), published_bam=str(bam),
                generated=True, stage='align-DNA', sample=name, aligner='BWA-MEM2',
                published_indexes=[str(paths[1])], published_checksums=[str(paths[2])],
                published_file_sizes={str(path): path.stat().st_size for path in paths}))
            manifest_path.write_text(json.dumps(manifest))
        # Fail only conversion planning for one workflow, and only alignment
        # planning for another. The other arm must still execute in each case.
        recovery_reference = root / 'recovery-reference.fa'
        shutil.copyfile(reference, recovery_reference)
        failed_manifest_path = root / 'planning_failure.json'
        failed_manifest = json.loads(failed_manifest_path.read_text())
        failed_manifest['entries'][0]['reference_fasta'] = str(recovery_reference)
        failed_manifest['entries'][0]['reference_fasta_fai'] = str(recovery_reference) + '.fai'
        failed_manifest_path.write_text(json.dumps(failed_manifest))
        (output / 'alignment_plan_failure.aligned.bam').write_text('changed alignment size')
        unlisted = output / 'unlisted.bam'
        unlisted.write_text('keep this file')
        keep_manifest = json.loads((root / 'keep.json').read_text())
        keep_record = records.record_path(keep_manifest, keep_manifest['entries'][0])
        (root / 'keep-record.txt').write_text(str(keep_record))
        wrappers = root / 'bin'
        wrappers.mkdir()
        sbatch = wrappers / 'sbatch'
        sbatch.write_text('#!/bin/bash\necho "Unexpected Slurm submission in local test" >&2\nexit 99\n')
        sbatch.chmod(0o755)
        wrapper = wrappers / 'samtools'
        conversion_log = root / 'conversions.log'
        resumed = root / 'resumed'
        wrapper.write_text('#!/bin/bash\nset -e\n'
                           + 'if [[ "$1" == view ]]; then\n'
                           + '  for arg in "$@"; do\n'
                           + '    if [[ "$arg" == -##idx##* ]]; then\n'
                           + '      bam=${arg#-##idx##}; printf "%s\\n" "${bam%.cram.crai.tmp}.bam" >> '
                           + shlex.quote(str(conversion_log)) + '\n'
                           + '    fi\n  done\nfi\n'
                           + shlex.quote(samtools) + ' "$@"\n'
                           + 'if [[ "$1" == flagstat && "$*" == *reference=* && ! -f '
                           + shlex.quote(str(resumed)) + ' ]] && grep -q /mismatch.cram.tmp .command.sh; '
                           + 'then echo mismatch; fi\n'
                           + 'if [[ "$1" == flagstat && ! -f ' + shlex.quote(str(resumed)) + ' ]]; then\n'
                           + '  if [[ -f bamstatsfail.json && "$*" != *reference=* ]]; then exit 46; fi\n'
                           + '  if [[ -f cramstatsfail.json && "$*" == *reference=* ]]; then exit 47; fi\n'
                           + 'fi\n')
        wrapper.chmod(0o755)
        for tool, fixture, code in (('sha512sum', 'hashfail', 45), ('tee', 'writefail', 48)):
            tool_wrapper = wrappers / tool
            tool_wrapper.write_text('#!/bin/bash\nset -e\n'
                                    + shlex.quote(shutil.which(tool)) + ' "$@"\n'
                                    + 'if [[ -f ' + fixture + '.json && ! -f '
                                    + shlex.quote(str(resumed)) + ' ]]; then exit ' + str(code) + '; fi\n')
            tool_wrapper.chmod(0o755)
        # Fail host cleanup after removing only the BAM. The task must fail and
        # resume the remaining sidecars without rerunning samtools conversion.
        rm_wrapper = wrappers / 'rm'
        rm_wrapper.write_text('#!/bin/bash\nset -e\n'
                              + 'if [[ ! -f ' + shlex.quote(str(resumed)) + ' ]]; then\n'
                              + '  for arg in "$@"; do\n'
                              + '    if [[ "$arg" == */interrupted.bam ]]; then /bin/rm -- "$arg"; exit 42; fi\n'
                              + '    if [[ "$arg" == */alignment_interrupted.aligned.bam ]]; then /bin/rm -- "$arg"; exit 44; fi\n'
                              + '  done\nfi\nexec /bin/rm "$@"\n')
        rm_wrapper.chmod(0o755)
        python_wrapper = wrappers / 'python3'
        python_wrapper.write_text('#!/bin/bash\nset -e\n'
                                  + 'if [[ "$2" == authorize && "$*" == *recordfail.json* && "$*" != *delete_alignment* && ! -f '
                                  + shlex.quote(str(resumed)) + ' ]]; then exit 43; fi\n'
                                  + 'exec ' + shlex.quote(sys.executable) + ' "$@"\n')
        python_wrapper.chmod(0o755)
        config = root / 'nextflow.config'
        config.write_text("includeConfig '" + str(REPO / 'config/default.config') + "'\n" + """
// This override deliberately follows includeConfig and is not supplied on the CLI.
params.finalization_executor = 'local'
params.finalization_queue = ''
params.finalization_cluster_options = ''
params.finalization_max_conversions = 1
params.finalization_max_alignment_deletions = 2
params.final_output_dir = "${projectDir}/main_workflow"
params.log_output_dir = "${projectDir}/logs"
params.resuming = false
profiles {
    testing {
        params.containerization_system = 'docker'
        params.container_mount_flag = '-v'
        docker.enabled = false
        singularity.enabled = false
        apptainer.enabled = false
    }
}
process {
    withName: 'convert_finalization_bam|keep_bam|stream_failures' {
        cpus = 1
        memory = '128 MB'
    }
}
""")
        workflow = root / 'test.nf'
        workflow.write_text("include { finalize_outputs } from '" + str(REPO / 'module/finalize_outputs') + "'\n"
                            + "include { convert_finalization_bam as stream_failures } from '" + str(REPO / 'module/convert_finalization_bam') + "'\n"
                            + "include { convert_finalization_bam as keep_bam } from '" + str(REPO / 'module/convert_finalization_bam') + "'\n" + """
import groovy.json.JsonSlurper
workflow {
    finalize_outputs(Channel.of(['good', file("${projectDir}/good.json")], ['mismatch', file("${projectDir}/mismatch.json")],
        ['interrupted', file("${projectDir}/interrupted.json")], ['recordfail', file("${projectDir}/recordfail.json")],
        ['alignment_only', file("${projectDir}/alignment_only.json")], ['alignment_interrupted', file("${projectDir}/alignment_interrupted.json")],
        ['planning_failure', file("${projectDir}/planning_failure.json")], ['alignment_plan_failure', file("${projectDir}/alignment_plan_failure.json")]))
    finalize_outputs.out.conversion_results.toList().view {
        assert it.collect { row -> row[0] }.sort() == (params.resuming ?
            ['interrupted', 'mismatch', 'planning_failure', 'recordfail'] : ['alignment_interrupted', 'alignment_plan_failure', 'good', 'mismatch'])
        assert it.every { row -> row[2].text == row[3].text }
        'Only completed conversions emit success'
    }
    finalize_outputs.out.completed_entries.toList().view {
        assert it.countBy { row -> row[0] } == (params.resuming ?
            [good: 2, interrupted: 2, mismatch: 3, recordfail: 2, alignment_only: 1, alignment_interrupted: 2, planning_failure: 2, alignment_plan_failure: 2] :
            [good: 2, mismatch: 2, interrupted: 1, recordfail: 1, alignment_only: 1, alignment_interrupted: 1, planning_failure: 1, alignment_plan_failure: 1])
        'Persisted and new completions are both emitted'
    }
    finalize_outputs.out.cleanup_results.toList().view {
        assert it.collect { row -> row[0] }.sort() == (params.resuming ?
            ['alignment_interrupted', 'alignment_plan_failure'] : ['alignment_only', 'good', 'interrupted', 'mismatch', 'planning_failure', 'recordfail'])
        'Alignment cleanup and conversion succeed independently of failures in the other arm'
    }
    def keep_manifest = file('keep.json')
    def keep_entry = new JsonSlurper().parse(keep_manifest).entries[0]
    keep_bam(Channel.of(['keep', keep_manifest, [entry_id: keep_entry.entry_id, entry: keep_entry, delete_source: false,
        manifest_path: keep_manifest.toAbsolutePath().toString(), record_path: file('keep-record.txt').text]]))
    keep_bam.out.completed.toList().view { assert it.size() == 1; 'Source retention flag respected' }
    def failures = ['hashfail', 'bamstatsfail', 'cramstatsfail', 'writefail'].collect { name ->
        def manifest = file("${projectDir}/${name}.json")
        def entry = new JsonSlurper().parse(manifest).entries[0]
        [name, manifest, [entry_id: entry.entry_id, entry: entry, delete_source: true,
            manifest_path: manifest.toAbsolutePath().toString()]]
    }
    stream_failures(Channel.fromList(failures))
    stream_failures.out.completed.toList().view {
        assert it.size() == (params.resuming ? 4 : 0)
        'Every stream consumer must succeed before completion'
    }
}
""")
        environment = dict(os.environ, PATH=str(wrappers) + os.pathsep + os.environ['PATH'])
        command = (sys.argv[1:] or ['nextflow']) + ['-C', str(config), 'run', str(workflow),
                                '-profile', 'testing', '-ansi-log', 'false', '-w', str(root / 'work'),
                                '--finalization_cram_recalibrated_bams', 'true',
                                '--finalization_delete_recalibrated_bams', 'true', '--finalization_delete_alignment_bams', 'true']
        result = subprocess.run(command, cwd=root, env=environment, timeout=180)
        if result.returncode:
            for error in (root / 'work').rglob('.command.err'):
                print(error.read_text())
            raise SystemExit(result.returncode)
        exit_codes = {(task / '.exitcode').read_text().strip() for task in (root / 'work').glob('*/*')
                      if (task / '.exitcode').exists()}
        assert {'42', '43', '44', '45', '46', '47', '48'}.issubset(exit_codes), exit_codes
        for name in stream_failures:
            manifest = json.loads((root / (name + '.json')).read_text())
            entry = manifest['entries'][0]
            assert records.load_record(manifest, entry)['state'] == 'pending'
            assert all(Path(path).exists() for path in entry['published_file_sizes'])
            assert not Path(entry['published_bam']).with_suffix('.cram').exists()
        for name in ('good', 'keep', 'mismatch', 'interrupted', 'recordfail', 'alignment_interrupted', 'planning_failure', 'alignment_plan_failure'):
            manifest = json.loads((root / (name + '.json')).read_text())
            if name != 'alignment_plan_failure':
                verify_manifest(manifest, manifest, name)
            saved = records.load_record(manifest, manifest['entries'][0])
            expected_state = {'mismatch': 'pending', 'recordfail': 'pending', 'interrupted': 'cleanup_pending'}.get(name, 'complete')
            if name == 'planning_failure':
                assert saved is None, saved
            else:
                assert saved['state'] == expected_state, saved
            bam = output / (name + '.bam')
            for source in (bam, Path(str(bam) + '.bai'), Path(str(bam) + '.sha512')):
                expected_exists = name not in ('good', 'alignment_interrupted', 'alignment_plan_failure') and not (name == 'interrupted' and source == bam)
                assert source.exists() == expected_exists, str(source)
            cram = bam.with_suffix('.cram')
            assert cram.exists() == (name not in ('mismatch', 'planning_failure'))
            if name not in ('mismatch', 'planning_failure'):
                assert Path(str(cram) + '.crai').exists()
                for sidecar in (str(cram) + '.sha512', str(cram) + '.crai.sha512'):
                    subprocess.run(['sha512sum', '--check', sidecar], cwd=output, check=True)
                decoded = subprocess.check_output([samtools, 'view', '--input-fmt-option', 'decode_md=0',
                                                   '-T', str(reference), str(cram)], text=True)
                assert decoded == ''.join(sam.read_text().splitlines(keepends=True)[2:])
        assert not list((root / 'work').rglob('*.bam')), 'BAM was staged'
        for name in alignment_workflows:
            manifest = json.loads((root / (name + '.json')).read_text())
            if name != 'alignment_plan_failure':
                verify_manifest(manifest, manifest, name)
            alignment = manifest['entries'][-1]
            record = records.load_record(manifest, alignment)
            notice = Path(alignment['published_bam'] + '.deleted.txt')
            assert notice.exists() == (name not in ('alignment_plan_failure', 'alignment_interrupted'))
            if notice.exists():
                assert notice.read_text() == (
                    'Alignment BAM deleted by the finalization workflow: ' + alignment['published_bam'] + '\n')
            if name == 'alignment_plan_failure':
                assert record is None
            elif name == 'alignment_interrupted':
                assert record['state'] == 'cleanup_pending'
            else:
                assert record['state'] == 'complete', 'Conversion failure blocked alignment cleanup'
            for source in alignment['published_file_sizes']:
                removed = name != 'alignment_plan_failure' and (name != 'alignment_interrupted' or source == alignment['published_bam'])
                assert Path(source).exists() != removed, source
        assert not (output / 'mismatch_good.bam').exists()
        assert unlisted.read_text() == 'keep this file'
        assert len(list((root / 'main_workflow/intermediate/output-finalization-plans').rglob('finalization_plan.json'))) == 14
        subprocess.run([samtools, 'faidx', str(recovery_reference)], check=True)
        (output / 'alignment_plan_failure.aligned.bam').write_text('alignment fixture')
        resumed.touch()
        subprocess.run(command + ['-resume', '--resuming', 'true'], cwd=root, env=environment, check=True, timeout=180)
        conversions = conversion_log.read_text().splitlines()
        for name in ('good', 'keep', 'mismatch', 'interrupted', 'recordfail', 'alignment_interrupted', 'planning_failure', 'alignment_plan_failure'):
            manifest = json.loads((root / (name + '.json')).read_text())
            verify_manifest(manifest, manifest, name)
            assert records.load_record(manifest, manifest['entries'][0])['state'] == 'complete'
            for source in manifest['entries'][0]['published_file_sizes']:
                assert Path(source).exists() == (name == 'keep')
            expected_count = 2 if name in ('mismatch', 'recordfail') else 1
            assert conversions.count(str(output / (name + '.bam'))) == expected_count, conversions
        for name in alignment_workflows:
            manifest = json.loads((root / (name + '.json')).read_text())
            verify_manifest(manifest, manifest, name)
            alignment = manifest['entries'][-1]
            assert Path(alignment['published_bam'] + '.deleted.txt').read_text() == (
                'Alignment BAM deleted by the finalization workflow: ' + alignment['published_bam'] + '\n')
            for entry in manifest['entries']:
                assert records.load_record(manifest, entry)['state'] == 'complete'
                assert all(not Path(source).exists() for source in entry['published_file_sizes'])
        for sidecar in output.glob('*.cram*.sha512'):
            subprocess.run(['sha512sum', '--check', str(sidecar)], cwd=output, check=True)
        for name in stream_failures:
            manifest = json.loads((root / (name + '.json')).read_text())
            entry = manifest['entries'][0]
            assert records.load_record(manifest, entry)['state'] == 'complete'
            assert all(not Path(path).exists() for path in entry['published_file_sizes'])
            assert conversions.count(entry['published_bam']) == 2
        assert conversions.count(str(output / 'mismatch_good.bam')) == 1
        for process in ('plan_output_finalization', 'convert_finalization_bam',
                        'delete_finalization_alignment_bams'):
            log_root = root / 'logs/process-log/finalize_outputs' / process
            assert list(log_root.glob('*/log.command.sh')), process
            assert list(log_root.glob('*/log.command.err')), process
        assert unlisted.read_text() == 'keep this file'
        print('Late executor override, independent planning failures, worker failures and resume passed')


if __name__ == '__main__':
    main()
