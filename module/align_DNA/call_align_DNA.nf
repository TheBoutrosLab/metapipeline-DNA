include { combine_input_with_params; generate_weblog_args } from '../common.nf'

/*
    Process to call the align-DNA pipeline.
*/
process call_align_DNA {
    cpus params.align_DNA.subworkflow_cpus

    publishDir "${params.output_dir}/output",
        mode: "copy",
        pattern: "align-DNA-*/*",
        failOnError: false

    publishDir path: "${params.metapipeline_log_output_dir}/process-log",
        mode: "copy",
        pattern: ".command.*",
        saveAs: { "${task.process.replace(':', '/')}-${sample}/log${file(it).getName()}" },
        failOnError: false

    input:
        tuple(
            val(patient),
            val(sample),
            val(state),
            path(input_csv)
        )
    
    output:
        // task.workDir is available when outputs are collected, after script rendering.
        tuple val(sample), path(output_directory), val({ manifest_context + [task_work_dir: task.workDir.toString()] }), emit: align_dna_output_directory
        path "align-DNA-*/*"
        path ".command.*"

    script:
    output_directory = "align-DNA-*/${sample}"
    manifest_context = [
        publish_root: "${params.output_dir}/output".toString(),
        run_id: params.task_hash,
        workflow_identifier: (params.sample_mode == 'single' ? sample : patient)
    ]

    String params_to_dump = combine_input_with_params(params.align_DNA.metapipeline_arg_map)
    String weblog_args = generate_weblog_args()
    """
    set -euo pipefail

    printf "${params_to_dump}" > combined_align_dna_params.yaml

    WORK_DIR=${params.work_dir}/work-align-DNA-${sample}
    mkdir \$WORK_DIR && chmod 2777 \$WORK_DIR
    nextflow run \
        ${moduleDir}/../../external/pipeline-align-DNA/main.nf \
        -params-file combined_align_dna_params.yaml \
        --sample_id ${sample} \
        --output_dir \$(pwd) \
        --work_dir \$WORK_DIR \
        --spark_temp_dir \$WORK_DIR \
        --input_csv ${input_csv} \
        --dataset_id ${params.project_id} \
        -profile "${params.containerization_system}" \
        -c ${moduleDir}/default.config ${weblog_args}

    rm -r \$WORK_DIR
    """
}
