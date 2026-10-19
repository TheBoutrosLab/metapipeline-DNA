nextflow.enable.dsl = 2

import groovy.json.JsonSlurper
import java.nio.file.Files

params.fixture_dir = Files.createTempDirectory('manifest-persistence-').toString()
params.test_empty = false
params.test_mode = 'paired'
params.test_invalid = false
params.task_hash = 'run-1'
params.patient = 'P1'
params.workflow_identifier = params.test_mode == 'single' ? 'T1' : 'P1'
params.output_dir = "${params.fixture_dir}/results"
params.output_manifest_path = "${params.fixture_dir}/outer-task/OUTPUTMANIFEST/output_manifest.json"

include { write_output_manifest } from '../../module/write_output_manifest'

workflow {
    def entry = { stage, sample, invocation ->
        def published = "${params.output_dir}/output/${stage}-1/${invocation}/${sample}.bam".toString()
        [schema_version: 1, run_id: params.task_hash,
            workflow_identifier: params.workflow_identifier, patient: params.patient,
            entry_id: published, published_bam: published,
            stage: stage, sample: sample, invocation_id: invocation]
    }
    def alignment = params.test_mode == 'single'
        ? [entry('align-DNA', 'T1', 'T1')]
        : ['N', 'T1', 'T2'].collect { entry('align-DNA', it, it) }
    def recalibration = params.test_mode == 'single'
        ? [entry('recalibrate-BAM', 'T1', 'T1')]
        : ['T1', 'T2'].collectMany { tumor ->
            [entry('recalibrate-BAM', 'N', tumor), entry('recalibrate-BAM', tumor, tumor)]
        }
    if (params.test_empty) {
        alignment = []
        recalibration = []
    }
    if (params.test_invalid) {
        recalibration[0].workflow_identifier = 'wrong-workflow'
    }
    // Multiple emissions per stage must produce exactly one manifest task.
    entries = Channel.fromList(alignment).mix(Channel.fromList(recalibration)).toList()
    write_output_manifest(entries)
    write_output_manifest.out.manifest.toList().view { files ->
        assert files.size() == 1
        def manifest = new JsonSlurper().parse(files[0].toFile())
        assert manifest.schema_version == 1
        assert manifest.workflow_identifier == params.workflow_identifier
        assert manifest.run_id == params.task_hash
        assert manifest.patient == params.patient
        assert manifest.entries.size() == (params.test_empty ? 0 : (params.test_mode == 'single' ? 2 : 7))
        def ids = manifest.entries.collect { it.entry_id }
        assert ids == ids.sort(false)
        assert ids.unique(false).size() == ids.size()
        "Manifest written: ${manifest.entries.size()} entries for ${manifest.workflow_identifier}"
    }
}
