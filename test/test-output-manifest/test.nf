nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import java.nio.file.Files


params.test_skipped = false
params.override_realignment = params.test_skipped
params.override_recalibrate_bam = params.test_skipped
params.primary_aligner = 'BWA-MEM2'
params.sample_mode = 'paired'
params.input_type = 'BAM'

def write_fixture(path, String contents = 'fixture') {
    Files.createDirectories(path.parent)
    Files.write(path, contents.bytes)
    return path
}

def root = Files.createTempDirectory('manifest-identification-')
params.work_dir = root.toString()
params.output_dir = root.resolve('published').toString()
params.pipeline_status_directory = root.resolve('status').toString()
Files.createDirectories(root.resolve('status'))
params.pipeline_predecessor = [:]
params.convert_BAM2FASTQ = [is_pipeline_enabled: false]
params.recalibrate_BAM = [is_pipeline_enabled: true]
params.sample_data = ['N', 'T1', 'T2'].collectEntries { sample ->
    [(sample): [patient: 'P', state: sample == 'N' ? 'normal' : 'tumor',
        original_data: [path: root.resolve("original-${sample}.bam").toString()],
        'align-DNA': ['BWA-MEM2': [:], 'HISAT2': [:]], 'recalibrate-BAM': [:]]]
}

include { identify_align_dna_outputs } from '../../module/align_DNA/identify_outputs'
include { identify_recalibrate_bam_outputs } from '../../module/recalibrate_BAM/identify_outputs'
include { align_DNA as skipped_alignment } from '../../module/align_DNA/workflow'
include { recalibrate_BAM as skipped_recalibration } from '../../module/recalibrate_BAM/workflow'
include { build_output_manifest_entry; read_recalibration_metadata } from '../../module/output_manifest'

workflow {

    if (params.test_skipped) {
        skipped_alignment(Channel.of('done'))
        skipped_recalibration(skipped_alignment.out.alignment_sample_data_updated)
        skipped_alignment.out.manifest_entries.toList().view { assert it == []; 'Skipped alignment: no manifest entries' }
        skipped_recalibration.out.manifest_entries.toList().view { assert it == []; 'Skipped recalibration: no manifest entries' }
        skipped_recalibration.out.recalibrate_sample_data_updated.view {
            assert it == 'done'
            assert params.sample_data.N['recalibrate-BAM'].BAM == params.sample_data.N.original_data.path
            'Skipped stages preserve original inputs'
        }
    } else {
        def context_for = { work_root -> [task_work_dir: work_root.toString(),
            publish_root: "${params.output_dir}/output".toString(), run_id: 'run-1', workflow_identifier: 'P'] }
        def alignment_inputs = ['N', 'T1', 'T2'].collect { sample ->
            def work_root = root.resolve("align-${sample}")
            def sample_dir = work_root.resolve("align-DNA-1/${sample}")
            ['BWA-MEM2', 'HISAT2'].each { aligner ->
                def bam = write_fixture(sample_dir.resolve("${aligner}-1/output/${aligner}-${sample}.bam"))
                write_fixture(bam.resolveSibling("${bam.fileName}.bai"))
                write_fixture(bam.resolveSibling("${bam.fileName}.sha512"))
                write_fixture(bam.resolveSibling("${bam.fileName}.bai.sha512"))
                write_fixture(bam.resolveSibling('unrelated.bam.sha512'))
            }
            [sample, sample_dir, context_for(work_root)]
        }

        def recalibration_inputs = ['T1', 'T2'].collect { tumor ->
            def work_root = root.resolve("recalibrate-${tumor}")
            def output = work_root.resolve("recalibrate-BAM-1/${tumor}/GATK-1/output")
            def qc = output.parent.resolve('QC/contamination_qc/run_CalculateContamination_GATK')
            ['N', tumor].each { sample ->
                def bam = write_fixture(output.resolve("BWA-MEM2-GATK-1-${sample}.bam"))
                write_fixture(bam.resolveSibling("${bam.fileName}.bai"))
                write_fixture(bam.resolveSibling("${bam.fileName}.sha512"))
                write_fixture(qc.resolve("GATK-1-${sample}_alone.table"))
                write_fixture(qc.resolve("GATK-1-${sample}_with-matched-normal.table"))
                // The existing duplicate-normal cleanup waits for the published copy.
                write_fixture(root.resolve('published/output').resolve(work_root.relativize(bam)))
            }
            def resolved = write_fixture(work_root.resolve('params.json'), JsonOutput.toJson([
                reference_fasta: '/actual/overridden.fa', reference_fasta_fai: '/actual/custom-index.fai', aligner: 'BWA-MEM2-1'
            ]))
            [[normal: ['N'], tumor: [tumor]], output, qc, context_for(work_root), resolved]
        }

        // Publication paths must follow the wrapper tree, even for symlinked BAMs
        // and output-root names containing spaces or regex metacharacters.
        def special_root = root.resolve('wrapper [a].b')
        def source = write_fixture(root.resolve('source.bam'))
        def link = special_root.resolve('align-DNA-1/N/BWA-MEM2-1/output/BWA-MEM2-N.bam')
        Files.createDirectories(link.parent)
        Files.createSymbolicLink(link, source)
        def identity = [patient: 'P', sample: 'N', stage: 'align-DNA', aligner: 'BWA-MEM2']
        def linked_entry = build_output_manifest_entry(link.toString(), context_for(special_root), identity)
        assert linked_entry.working_bam == source.toString()
        assert linked_entry.published_bam == "${params.output_dir}/output/align-DNA-1/N/BWA-MEM2-1/output/BWA-MEM2-N.bam"
        assert linked_entry.published_indexes == []
        // Invoke negative cases directly: Nextflow aborts on an exception from
        // an imported function even when the caller catches that exception.
        def helpers = new GroovyShell().parse(new File("${projectDir}/../../module/output_manifest.nf"))
        try {
            helpers.build_output_manifest_entry(source.toString(), context_for(special_root), identity)
            assert false: 'Accepted a file outside the wrapper root'
        } catch (IllegalArgumentException expected) { }
        def invalid_params = write_fixture(root.resolve('invalid-params.json'), '{}')
        try {
            helpers.read_recalibration_metadata(invalid_params.toString(), context_for(special_root))
            assert false: 'Accepted an unknown reference'
        } catch (IllegalArgumentException expected) { }

        identify_align_dna_outputs(Channel.fromList(alignment_inputs))
        identify_align_dna_outputs.out.manifest_entries.toList().view { entries ->
            assert entries.size() == 6
            assert entries.every { it.run_id == 'run-1' && it.patient == 'P' && it.generated }
            assert entries.every { it.published_indexes.size() == 1 && it.published_checksums.size() == 2 }
            assert entries.every { it.published_file_sizes.size() == 4 && it.published_file_sizes.values().every { size -> size == 7 } }
            assert entries.every { !it.published_checksums.any { path -> path.contains('unrelated') } }
            assert entries.collect { it.entry_id }.unique().size() == 6
            assert JsonOutput.toJson(entries).contains('published_bam')
            'Alignment manifest: six entries, two aligners, exact sidecars'
        }
        recalibration_ready = identify_align_dna_outputs.out.och_align_dna_outputs_identified
            .flatMap { assert it == 'done'; recalibration_inputs }
        identify_recalibrate_bam_outputs(recalibration_ready)
        identify_recalibrate_bam_outputs.out.manifest_entries.toList().view { entries ->
            assert entries.size() == 4
            assert entries.findAll { it.sample == 'N' }.size() == 2
            assert entries.collect { it.entry_id }.unique().size() == 4
            assert entries.every { it.reference_fasta == '/actual/overridden.fa' && it.reference_fasta_fai == '/actual/custom-index.fai' }
            assert entries.every { it.aligner == 'BWA-MEM2-1' }
            assert entries.every { it.source_alignment.entry_id == params.sample_data[it.sample]['align-DNA']['BWA-MEM2'].manifest_entry_id }
            def normals = entries.findAll { it.sample == 'N' }
            assert normals.count { new File(it.working_bam).exists() } == 1
            assert normals.every { new File(it.published_bam).exists() }
            assert normals.every { it.published_file_sizes[it.published_bam] == 7 }
            'Recalibration manifest: all four outputs retained, including duplicate normal'
        }
        identify_recalibrate_bam_outputs.out.och_recalibrate_bam_identified.view {
            assert it == 'done'
            assert params.sample_data.T1['recalibrate-BAM'].matched_normal_contamination.endsWith('_with-matched-normal.table')
            'Existing completion signals and sample data preserved'
        }
    }
}
