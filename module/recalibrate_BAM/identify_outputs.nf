include { identify_file; delete_file } from '../common'
include { build_output_manifest_entry; read_recalibration_metadata } from '../output_manifest'
include { sanitize_string } from '../../external/pipeline-Nextflow-module/modules/common/generate_standardized_filename/main.nf'

workflow identify_recalibrate_bam_outputs {
    take:
    och_recalibrate_bam

    main:
    och_recalibrate_bam.map{ recalibrate_bam_out ->
        def entries = []
        def reference = read_recalibration_metadata(recalibrate_bam_out[4].toString(), recalibrate_bam_out[3])
        def record_bam = { sample ->
            String bam = identify_file("${recalibrate_bam_out[1]}/*GATK-*${sanitize_string(sample)}*.bam", false)
            def entry = build_output_manifest_entry(bam, recalibrate_bam_out[3], [
                patient: params.sample_data[sample].patient,
                sample: sample, stage: 'recalibrate-BAM', aligner: params.primary_aligner
            ]) + reference
            def alignment = params.sample_data[sample]['align-DNA'][params.primary_aligner]
            entry.source_alignment = [
                entry_id: params.override_realignment ? null : alignment.manifest_entry_id,
                working_bam: alignment.BAM
            ]
            entries.add(entry)
            return entry.working_bam
        }
        recalibrate_bam_out[0].normal.each { normal_sample ->
            // Record every published copy before duplicate working normals are deleted.
            String bam_file = record_bam(normal_sample);
            if (!params.sample_data[normal_sample]['recalibrate-BAM']['BAM']) {
                String sample_alone_contamination = identify_file("${recalibrate_bam_out[2]}/GATK-*${sanitize_string(normal_sample)}_alone.table");
                params.sample_data[normal_sample]['recalibrate-BAM']['BAM'] = bam_file;
                params.sample_data[normal_sample]['recalibrate-BAM']['sample_alone_contamination'] = sample_alone_contamination;
            } else {
                // Normal file already found so delete any other normals - only triggered when running multiple samples in paired mode

                // Replace the work_dir prefix in the output path with the output_dir prefix for final output
                String separator = "/recalibrate-BAM-";
                String dir_stripped = recalibrate_bam_out[1].toString().replaceFirst("${params.work_dir}.*${separator}", "");
                delete_file(bam_file, "${params.output_dir}/output${separator}${dir_stripped}/*GATK-*${sanitize_string(normal_sample)}*.bam");
            };
        };
        recalibrate_bam_out[0].tumor.each { tumor_sample ->
            String sample_alone_contamination = identify_file("${recalibrate_bam_out[2]}/GATK-*${sanitize_string(tumor_sample)}_alone.table");
            String matched_normal_contamination = '';
            if (params.sample_mode != 'single') {
                matched_normal_contamination = identify_file("${recalibrate_bam_out[2]}/GATK-*${sanitize_string(tumor_sample)}_with-matched-normal.table");
            }
            params.sample_data[tumor_sample]['recalibrate-BAM']['BAM'] = record_bam(tumor_sample);
            params.sample_data[tumor_sample]['recalibrate-BAM']['sample_alone_contamination'] = sample_alone_contamination;
            params.sample_data[tumor_sample]['recalibrate-BAM']['matched_normal_contamination'] = matched_normal_contamination;
        };

        return entries
    }
    .set{ identified_outputs }

    identified_outputs.flatMap{ it }.set{ manifest_entries }
    identified_outputs
        .collect()
        .map{ 'done' }
        .set{ och_recalibrate_bam_identified }

    emit:
    manifest_entries = manifest_entries
    och_recalibrate_bam_identified = och_recalibrate_bam_identified
}
