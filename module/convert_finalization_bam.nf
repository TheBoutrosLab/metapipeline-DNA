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

    container 'ghcr.io/theboutroslab/samtools:1.24'

    cpus 4
    memory '8 GB'

    maxForks params.finalization_max_conversions

    cache false // Persistent records, rather than cached task outputs, decide what remains.
    errorStrategy 'ignore'

    // These hooks run on the Slurm host, within this task. The samtools image
    // has no Python, so host Python 3 is required. Finalization runs for the
    // same entry are assumed not to overlap.
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

    script:
    def entry = request.entry
    def bam = entry.published_bam
    def cram = bam.replaceFirst(/\.bam$/, '.cram')
    def reference = entry.reference_fasta + '##idx##' + entry.reference_fasta_fai
    """
    set -euo pipefail

    if [ "\$(cat finalization.action)" != convert ]; then
        touch conversion.ready
        exit 0
    fi

    samtools view -@ ${task.cpus - 1} -T ${conversion_quote(reference)} \\
        -O CRAM,store_md=1,store_nm=1 --write-index \\
        -o ${conversion_quote(cram + '.tmp##idx##' + cram + '.crai.tmp')} ${conversion_quote(bam)}

    samtools flagstat -@ ${task.cpus - 1} ${conversion_quote(bam)} > bam.flagstat

    samtools flagstat -@ ${task.cpus - 1} --input-fmt-option ${conversion_quote('reference=' + reference)} \\
        ${conversion_quote(cram + '.tmp')} > cram.flagstat

    diff -u bam.flagstat cram.flagstat
    mv -- ${conversion_quote(cram + '.tmp')} ${conversion_quote(cram)}
    mv -- ${conversion_quote(cram + '.crai.tmp')} ${conversion_quote(cram + '.crai')}
    samtools --version > samtools.version
    touch conversion.ready
    """
}
