include { identify_file } from '../common'
include { sanitize_string } from '../../external/pipeline-Nextflow-module/modules/common/generate_standardized_filename/main.nf'

workflow identify_call_gsnp_outputs {
    take:
    och_call_gsnp

    main:
    och_call_gsnp.map{ call_gsnp_out ->
        def run_id = call_gsnp_out[0];
        def sample_ids = call_gsnp_out[1];
        def sanitized_run_id = sanitize_string(run_id);
        def gsnp_output_dir = new File(call_gsnp_out[2].toString());
        def gsnp_output_pattern = /(.*)-([\d\.]*)$/;

        def outputs_to_check = [];
        def match = null;

        gsnp_output_dir.eachFile { file ->
            match = (file.name =~ gsnp_output_pattern);
            if (match) {
                outputs_to_check << [match[0][1], file.name];
            }
        }

        // HaplotypeCaller produces one joint VCF for the run. In multi mode,
        // assign it to the first normal sample; otherwise assign it to the run ID.
        def haplotypecaller_id_to_assign = run_id;
        if (params.sample_mode == 'multi') {
            haplotypecaller_id_to_assign = sample_ids.find{ params.sample_data[it]['state'] == 'normal' };
        }

        outputs_to_check.each { output_tool, output_dir_name ->
            if (output_tool == 'GATK') {
                params.sample_data[haplotypecaller_id_to_assign]['call-gSNP']['HaplotypeCaller'] = identify_file("${gsnp_output_dir}/${output_dir_name}/output/GATK-*_${sanitized_run_id}_snv.vcf.gz");
            } else if (output_tool == 'DeepVariant') {
                // DeepVariant produces one VCF per BAM/sample rather than one
                // VCF named with the run-level patient ID.
                sample_ids.each { raw_sample_id ->
                    def sanitized_sample_id = sanitize_string(raw_sample_id);
                    params.sample_data[raw_sample_id]['call-gSNP']['DeepVariant'] = identify_file("${gsnp_output_dir}/${output_dir_name}/output/DeepVariant-*_${sanitized_sample_id}.vcf.gz");
                }
            }
        }

        return 'done';
    }
    .collect()
    .map{ 'done' }
    .set{ och_call_gsnp_identified }

    emit:
    och_call_gsnp_identified = och_call_gsnp_identified
}
