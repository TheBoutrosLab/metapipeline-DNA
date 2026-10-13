import groovy.json.JsonSlurper
include { convert_finalization_bam } from './convert_finalization_bam'
include { delete_finalization_alignment_bams } from './delete_finalization_alignment_bams'


def quote_finalization_arg(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

process plan_output_finalization {
    // Published files and finalization records can change without the manifest.
    cache false

    tag { identifier }

    publishDir path: "${params.final_output_dir}/intermediate/output-finalization-plans",
        mode: 'copy',
        pattern: 'finalization_plan.json',
        overwrite: true,
        saveAs: { "${identifier}/${task.hash.replace('/', '-')}/finalization_plan.json" }

    input:
    tuple val(identifier), path(output_manifest)

    output:
    tuple val(identifier), path(output_manifest), path('finalization_plan.json'), emit: plan

    script:
    def options = []
    if (params.finalization_cram_recalibrated_bams) options.add('--cram-recalibrated-bams')
    if (params.finalization_delete_alignment_bams) options.add('--delete-alignment-bams')
    if (params.finalization_delete_recalibrated_bams) options.add('--delete-recalibrated-bams')
    """
    python3 ${quote_finalization_arg("${moduleDir}/../templates/plan_output_finalization.py")} \\
        --manifest ${quote_finalization_arg(output_manifest)} \\
        --identifier ${quote_finalization_arg(identifier)} ${options.join(' ')}
    """
}

workflow finalize_outputs {
    take:
    verified_manifests

    main:
    def enabled = ['finalization_cram_recalibrated_bams', 'finalization_delete_alignment_bams',
                   'finalization_delete_recalibrated_bams'].any { params.containsKey(it) && params[it] }
    plans = Channel.empty()
    conversion_requests = Channel.empty()
    conversion_results = Channel.empty()
    cleanup_requests = Channel.empty()
    cleanup_results = Channel.empty()
    completed_entries = Channel.empty()
    if (enabled) {
        plan_output_finalization(verified_manifests)
        plans = plan_output_finalization.out.plan
        parsed = plans.map { identifier, manifest, plan_file ->
            def plan = new JsonSlurper().parse(plan_file)
            log.info "Finalization plan for ${identifier}: ${plan.conversions.size()} conversions, ${plan.cleanup.entries.size()} cleanup entries, ${plan.completed.size()} complete"
            [identifier, manifest, plan]
        }
        // Only the JSON manifest is a path input. BAM/CRAM/reference locations
        // remain strings inside request maps; workers access shared paths.
        conversion_requests = parsed.flatMap { identifier, manifest, plan ->
            plan.conversions.collect { request ->
                // beforeScript needs the original path before input staging.
                [identifier, manifest, request + [manifest_path: manifest.toAbsolutePath().toString()]]
            }
        }
        convert_finalization_bam(conversion_requests)
        conversion_results = convert_finalization_bam.out.completed
        cleanup_requests = parsed.filter { identifier, manifest, plan -> !plan.cleanup.entries.isEmpty() }
            .map { identifier, manifest, plan -> [identifier, manifest, plan.cleanup] }

        // Both arms start from the verified manifest's plan independently.
        delete_finalization_alignment_bams(cleanup_requests)
        cleanup_results = delete_finalization_alignment_bams.out.completed
        completed_entries = parsed.flatMap { identifier, manifest, plan ->
            plan.completed.collect { entry_id -> [identifier, entry_id] }
        }.mix(conversion_results.map { identifier, entry_id, bam_stats, cram_stats -> [identifier, entry_id] })
            .mix(cleanup_results.flatMap { identifier, entry_ids -> entry_ids.collect { entry_id -> [identifier, entry_id] } })
    }

    emit:
    plans = plans
    conversion_requests = conversion_requests
    conversion_results = conversion_results
    cleanup_requests = cleanup_requests
    cleanup_results = cleanup_results
    completed_entries = completed_entries
}
