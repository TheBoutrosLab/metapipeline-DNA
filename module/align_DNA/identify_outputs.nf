include { identify_file } from '../common'
include { build_output_manifest_entry } from '../output_manifest'
include { sanitize_string } from '../../external/pipeline-Nextflow-module/modules/common/generate_standardized_filename/main.nf'

workflow identify_align_dna_outputs {
    take:
    och_align_dna

    main:
    och_align_dna.map{ align_dna_out ->
        def entries = []
        params.sample_data[align_dna_out[0]]['align-DNA'].each { aligner_tool, aligner_output ->
            String bam_file = identify_file("${align_dna_out[1]}/${aligner_tool}*/output/${aligner_tool}*${sanitize_string(align_dna_out[0])}.bam", false)
            def entry = build_output_manifest_entry(bam_file, align_dna_out[2], [
                patient: params.sample_data[align_dna_out[0]].patient,
                sample: align_dna_out[0], stage: 'align-DNA', aligner: aligner_tool
            ])
            aligner_output['BAM'] = entry.working_bam
            aligner_output['manifest_entry_id'] = entry.entry_id
            entries.add(entry)
        };

        return entries
    }
    .set{ identified_outputs }

    identified_outputs.flatMap{ it }.set{ manifest_entries }
    identified_outputs
    .collect()
    .map{ 'done' }
    .set{ och_align_dna_outputs_identified }

    emit:
    manifest_entries = manifest_entries
    och_align_dna_outputs_identified = och_align_dna_outputs_identified
}
