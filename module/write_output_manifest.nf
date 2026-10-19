import groovy.json.JsonOutput
include { combine_output_manifest_entries } from './output_manifest'

/*
 * Persist one manifest per nested patient/sample workflow. The first copy is
 * the handoff to the outer status check; the second is retained with results.
 * Neither copy is a workflow-success marker.
 */
process write_output_manifest {
    publishDir path: file(params.output_manifest_path).parent.toString(),
        mode: 'copy',
        pattern: 'output_manifest.json',
        saveAs: { file(params.output_manifest_path).name },
        overwrite: true

    publishDir path: "${params.output_dir}/intermediate/output-manifests/${params.workflow_identifier}/${params.task_hash}",
        mode: 'copy',
        pattern: 'output_manifest.json',
        overwrite: true

    input:
    val(manifest_entries)

    output:
    path('output_manifest.json'), emit: manifest

    exec:
    def metadata = [
        schema_version: 1,
        run_id: params.task_hash.toString(),
        workflow_identifier: params.workflow_identifier.toString(),
        patient: params.patient.toString(),
        publish_root: file("${params.output_dir}/output").toAbsolutePath().normalize().toString()
    ]
    def manifest = combine_output_manifest_entries(manifest_entries, metadata)
    new File("${task.workDir}/output_manifest.json").text = JsonOutput.prettyPrint(JsonOutput.toJson(manifest)) + '\n'
}
