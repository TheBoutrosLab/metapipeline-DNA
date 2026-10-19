import groovy.json.JsonSlurper
import java.nio.file.Files
import java.nio.file.Paths

/** Combine entries from all invocations of one patient/sample workflow. */
Map combine_output_manifest_entries(List entries, Map metadata) {
    def entry_ids = [] as Set
    entries.each { entry ->
        if (entry.schema_version != metadata.schema_version ||
            entry.run_id != metadata.run_id ||
            entry.workflow_identifier != metadata.workflow_identifier ||
            entry.patient != metadata.patient) {
            throw new IllegalArgumentException("Manifest entry belongs to a different workflow or schema: ${entry.entry_id}")
        }
        if (!entry.entry_id || !entry_ids.add(entry.entry_id)) {
            throw new IllegalArgumentException("Missing or duplicate manifest entry ID: ${entry.entry_id}")
        }
    }
    return metadata + [entries: entries.sort(false) { a, b -> a.entry_id <=> b.entry_id }]
}

/**
 * Describe a generated BAM without waiting for asynchronous publication. Paths
 * are mapped relative to the wrapper's explicit output root, before resolving
 * symlinks for working-file provenance. No file contents are read or hashed.
 */
Map build_output_manifest_entry(String bam, Map context, Map identity) {
    def work_root = Paths.get(context.task_work_dir.toString()).toAbsolutePath().normalize()
    def publish_root = Paths.get(context.publish_root.toString()).toAbsolutePath().normalize()
    def bam_path = Paths.get(bam).toAbsolutePath().normalize()
    if (!bam_path.startsWith(work_root)) {
        throw new IllegalArgumentException("Manifest BAM is outside the wrapper output root: ${bam}; expected work_root: ${work_root}")
    }
    def relative_bam = work_root.relativize(bam_path)
    if (!relative_bam.getName(0).toString().startsWith("${identity.stage}-")) {
        throw new IllegalArgumentException("Manifest BAM does not belong to ${identity.stage}: ${bam}")
    }
    String published_bam = publish_root.resolve(relative_bam).toString()
    String stem = bam_path.fileName.toString().replaceFirst(/\.bam$/, '')
    def index_candidates = ["${stem}.bam.bai", "${stem}.bai", "${stem}.bam.csi", "${stem}.csi"]
        .collect { bam_path.resolveSibling(it.toString()) }
    def checksum_candidates = ([bam_path] + index_candidates).collect { it.resolveSibling("${it.fileName}.sha512") }
    def published_existing = { candidates ->
        candidates.findAll { Files.isRegularFile(it) }
            .collect { publish_root.resolve(work_root.relativize(it)).toString() }
    }
    def published_file_sizes = ([bam_path] + index_candidates + checksum_candidates)
        .findAll { Files.isRegularFile(it) }
        .collectEntries { [(publish_root.resolve(work_root.relativize(it)).toString()): Files.size(it)] }

    return [
        schema_version: 1,
        entry_id: published_bam,
        run_id: context.run_id.toString(),
        workflow_identifier: context.workflow_identifier.toString(),
        invocation_id: context.task_work_dir.toString(),
        patient: identity.patient.toString(),
        sample: identity.sample.toString(),
        stage: identity.stage.toString(),
        aligner: identity.aligner.toString(),
        generated: true,
        working_bam: bam_path.toRealPath().toString(),
        published_bam: published_bam,
        published_indexes: published_existing(index_candidates),
        published_checksums: published_existing(checksum_candidates),
        published_file_sizes: published_file_sizes
    ]
}

/** Read the child's resolved parameters, including defaults and overrides. */
Map read_recalibration_metadata(String params_json, Map context) {
    def child_params = new JsonSlurper().parse(new File(params_json))
    def reference = [:]
    ['reference_fasta', 'reference_fasta_fai', 'aligner'].each { key ->
        def value = child_params[key]
        if (!(value instanceof String) || value.trim().isEmpty()) {
            throw new IllegalArgumentException("Missing resolved ${key} in ${params_json}")
        }
        reference[key] = key == 'aligner' ? value : Paths.get(context.task_work_dir.toString()).resolve(value).toAbsolutePath().normalize().toString()
    }
    return reference
}
