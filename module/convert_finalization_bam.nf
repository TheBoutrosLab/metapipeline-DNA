def conversion_quote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

def conversion_record_command(helper, manifest, request, step) {
    return "python3 ${conversion_quote(helper)} ${step} --manifest ${conversion_quote(manifest)} " +
        "--entry-id ${conversion_quote(request.entry_id)} ${request.delete_source ? '--delete-source' : ''}"
}

process convert_finalization_bam {
    // The tag also appears in the scheduler job name; avoid paths and whitespace.
    tag { "${identifier}_${request.entry.sample}".replaceAll(/[^A-Za-z0-9_.-]/, '_') }

    label 'finalization_executor'

    publishDir path: "${params.log_output_dir}/process-log",
        mode: 'copy',
        pattern: '.command.*',
        saveAs: { "${task.process.replace(':', '/')}/${identifier}-${request.entry.sample.toString().replaceAll(/[^A-Za-z0-9_.-]/, '_')}-${task.hash.replace('/', '-')}/log${file(it).name}" },
        failOnError: false

    container 'ghcr.io/theboutroslab/samtools:1.24'

    cpus 12
    memory '16 GB'

    maxForks params.finalization_max_conversions

    cache false // Persistent records, rather than cached task outputs, decide what remains.
    errorStrategy 'ignore'

    // These hooks run on the Slurm host, within this task.
    // Finalization runs for the same entry are assumed not to overlap.
    beforeScript {
        def helper = "${moduleDir}/../templates/finalization_records.py"
        """
        rm -f conversion.ready
        ${conversion_record_command(helper, request.manifest_path, request, 'prepare')} || exit \$?
        """
    }

    afterScript {
        def helper = "${moduleDir}/../templates/finalization_records.py"
        def entry = request.entry
        def sources = ([entry.published_bam] + entry.published_indexes + entry.published_checksums)
            .collect { conversion_quote(it) }.join(' ')
        """
        if [ "\${nxf_main_ret:-0}" -eq 0 ] && [ -f conversion.ready ]; then
            ${conversion_record_command(helper, request.manifest_path, request, 'authorize')} &&
                ${request.delete_source ? "rm -f -- ${sources}" : ':'} &&
                ${conversion_record_command(helper, request.manifest_path, request, 'complete')} ||
                { nxf_main_ret=\$?; exit "\$nxf_main_ret"; }
        fi
        """
    }

    // Bind the published files directly; do not stage/copy large BAMs.
    ext containerOptions: {
        def entry = request.entry
        def paths = [entry.published_bam, entry.reference_fasta, entry.reference_fasta_fai] +
                    entry.published_indexes + entry.published_checksums
        def directories = paths.collect { file(it).parent } +
                          [entry.reference_fasta, entry.reference_fasta_fai].collect { file(it).toRealPath().parent }
        directories.unique().collect { "${params.container_mount_flag} ${conversion_quote(it.toString() + ':' + it.toString())}" }.join(' ')
    }

    input:
    tuple val(identifier), path(output_manifest), val(request)

    output:
    tuple val(identifier), val(request.entry_id), path('bam.flagstat'), path('cram.flagstat'), emit: completed
    path('.command.*'), emit: logs

    script:
    def entry = request.entry
    def bam = entry.published_bam
    def cram = bam.replaceFirst(/\.bam$/, '.cram')
    def reference = entry.reference_fasta + '##idx##' + entry.reference_fasta_fai
    // Leave CPU capacity for both flagstats consumers, hashing and stream copies.
    def conversion_threads = Math.max(0, task.cpus - 7)
    """
    set -euo pipefail

    if [ "\$(cat finalization.action)" != convert ]; then
        touch conversion.ready
        exit 0
    fi

    pipe_dir=\$(mktemp -d ./finalization-pipes.XXXXXXXXXX)
    trap 'kill "\${bam_stats_pid:-}" "\${hash_pid:-}" 2>/dev/null || true; rm -rf -- "\$pipe_dir"' EXIT
    mkfifo "\$pipe_dir/bam_stats" "\$pipe_dir/cram_hash"

    samtools flagstat -@ 1 - < "\$pipe_dir/bam_stats" > bam.flagstat &
    bam_stats_pid=\$!
    sha512sum < "\$pipe_dir/cram_hash" > cram.sha512 &
    hash_pid=\$!

    tee "\$pipe_dir/bam_stats" < ${conversion_quote(bam)} |
        samtools view -@ ${conversion_threads} -T ${conversion_quote(reference)} \\
            -O CRAM,store_md=1,store_nm=1 --write-index \\
            -o ${conversion_quote('-##idx##' + cram + '.crai.tmp')} - |
        tee ${conversion_quote(cram + '.tmp')} "\$pipe_dir/cram_hash" |
        samtools flagstat -@ 1 --input-fmt-option ${conversion_quote('reference=' + reference)} - > cram.flagstat

    # Background consumers are not covered by pipefail; check each explicitly.
    wait "\$bam_stats_pid"
    wait "\$hash_pid"
    diff -u bam.flagstat cram.flagstat

    sha512sum < ${conversion_quote(cram + '.crai.tmp')} > crai.sha512
    read -r cram_digest _ < cram.sha512
    read -r crai_digest _ < crai.sha512
    printf '%s  %s\\n' "\$cram_digest" ${conversion_quote(file(cram).name)} > ${conversion_quote(cram + '.sha512.tmp')}
    printf '%s  %s\\n' "\$crai_digest" ${conversion_quote(file(cram).name + '.crai')} > ${conversion_quote(cram + '.crai.sha512.tmp')}

    mv -- ${conversion_quote(cram + '.tmp')} ${conversion_quote(cram)}
    mv -- ${conversion_quote(cram + '.crai.tmp')} ${conversion_quote(cram + '.crai')}
    mv -- ${conversion_quote(cram + '.sha512.tmp')} ${conversion_quote(cram + '.sha512')}
    mv -- ${conversion_quote(cram + '.crai.sha512.tmp')} ${conversion_quote(cram + '.crai.sha512')}
    samtools --version > samtools.version
    touch conversion.ready
    """
}
