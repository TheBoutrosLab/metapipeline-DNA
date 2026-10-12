def conversion_quote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

process convert_finalization_bam {
    tag { "${identifier}: ${request.entry_id}" }
    executor params.finalization_executor
    queue params.finalization_queue
    clusterOptions params.finalization_cluster_options
    container 'ghcr.io/theboutroslab/samtools:1.23'
    cpus 4
    memory '8 GB'
    time '24h'
    maxForks params.finalization_max_conversions
    scratch false
    errorStrategy 'ignore'

    // Bind the published files directly; do not stage/copy large BAMs.
    ext containerOptions: {
        def entry = request.entry
        def paths = [entry.published_bam, entry.reference_fasta, entry.reference_fasta_fai] +
                    entry.published_indexes + entry.published_checksums
        def directories = paths.collect { file(it).parent } +
                          [entry.reference_fasta, entry.reference_fasta_fai].collect { file(it).toRealPath().parent }
        def flag = params.containerization_system == 'docker' ? '-v' : '-B'
        directories.unique().collect { "${flag} ${conversion_quote(it.toString() + ':' + it.toString())}" }.join(' ')
    }
    containerOptions { task.ext.containerOptions }

    input:
    tuple val(identifier), path(output_manifest), val(request)

    output:
    tuple val(identifier), val(request.entry_id), path('bam.flagstat'), path('cram.flagstat'), emit: completed

    script:
    def entry = request.entry
    def bam = entry.published_bam
    def cram = bam.replaceFirst(/\.bam$/, '.cram')
    def reference = entry.reference_fasta + '##idx##' + entry.reference_fasta_fai
    def sources = ([bam] + entry.published_indexes + entry.published_checksums).collect { conversion_quote(it) }.join(' ')
    """
    set -euo pipefail

    samtools view -@ ${task.cpus - 1} -T ${conversion_quote(reference)} \\
        -O CRAM,store_md=1,store_nm=1 --write-index \\
        -o ${conversion_quote(cram + '.tmp##idx##' + cram + '.crai.tmp')} ${conversion_quote(bam)}

    samtools flagstat -@ ${task.cpus - 1} ${conversion_quote(bam)} > bam.flagstat

    samtools flagstat -@ ${task.cpus - 1} --input-fmt-option ${conversion_quote('reference=' + reference)} \\
        ${conversion_quote(cram + '.tmp')} > cram.flagstat

    diff -u bam.flagstat cram.flagstat
    mv -- ${conversion_quote(cram + '.tmp')} ${conversion_quote(cram)}
    mv -- ${conversion_quote(cram + '.crai.tmp')} ${conversion_quote(cram + '.crai')}
    ${request.delete_source ? "rm -- ${sources}" : ':'}
    """
}
