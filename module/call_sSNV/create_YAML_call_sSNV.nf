import org.yaml.snakeyaml.Yaml
include { sanitize_string } from '../../external/pipeline-Nextflow-module/modules/common/generate_standardized_filename/main.nf'

/*
* Create input YAML file for the call-sSNV pipeline.
*
* Input:
*   A tuple of five items:
*     @param META (Map): Metadata
*     @param sample_id (String): Sample ID to be used for run
*     @param normal_bam (List): Normal sample ID, BAM path, and optional contamination table
*     @param tumor_bam (List): Lists of tumor sample IDs, BAM paths, and optional contamination tables
*     @param algorithms (String): Comma-separated list of algorithms to run
*
* Output:
*   @return A tuple of 3 items, inlcuding the sample_id, algorithms, and the input YAML file created for the call-sSNV pipeline.
*/
process create_YAML_call_sSNV {
    publishDir "${params.output_dir}/intermediate/${task.process.replace(':', '/')}-${params.patient}/${sample_id}",
        pattern: 'call_sSNV_input.yaml',
        mode: 'copy'

    input:
        tuple(
            val(META), val(sample_id), val(normal_bam), val(tumor_bam), val(algorithms)
        )

    output:
        tuple(
            val(META),
            val(sample_id),
            val(algorithms),
            path(input_yaml)
        )

    exec:
    input_yaml = 'call_sSNV_input.yaml'
    create_bam_entry = { bam_input ->
        Map bam_entry = ['BAM': "${bam_input[1]}" as String]
        if (bam_input[2] != 'NO_TABLE.table') {
            bam_entry['contamination_table'] = "${bam_input[2]}" as String
        }
        return bam_entry
    }
    param_tumor_bams = tumor_bam.collect{ create_bam_entry(it) }
    param_normal_bam = normal_bam.collect{ create_bam_entry(it) }

    param_force_normal_only = META.param_force_normal_only
    param_force_tumor_only = META.param_force_tumor_only

    if (params.sample_mode == 'single' || param_force_normal_only || param_force_tumor_only) {
        // TO-DO: Use exact sample type when call-sSNV explicitly supports normal-only mode
        param_single_sample_data = (META.param_single_sample_type == 'normal') ? param_normal_bam : param_tumor_bams
        input_map = [
            'patient_id': sample_id,
            'input': [
                'tumor' : param_single_sample_data
            ]
        ]
    } else {
        input_map = [
            'patient_id': sample_id,
            'input': [
                'normal': param_normal_bam,
                'tumor': param_tumor_bams
            ]
        ]
    }

    base_sample_id = (param_force_tumor_only) ? "${sample_id}-TumorOnly" : sample_id
    META['param_output_dir_name'] = (base_sample_id == params.patient) ? base_sample_id : sanitize_string(base_sample_id)

    if (param_force_normal_only) {
        input_map = input_map + ['mutect2_pon_mode': true]
    }

    if (param_force_tumor_only) {
        input_map = input_map + ['sample_dir_name': "${META.param_output_dir_name}" as String]
    }

    Yaml yaml = new Yaml()
    yaml.dump(input_map, new FileWriter("${task.workDir}/${input_yaml}"))
}
