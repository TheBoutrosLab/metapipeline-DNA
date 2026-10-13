def cleanup_quote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

process delete_finalization_alignment_bams {
    tag { identifier }

    label 'finalization_executor'

    cpus 1
    memory '1 GB'

    maxForks params.finalization_max_alignment_deletions

    cache false
    errorStrategy 'ignore'

    input:
    tuple val(identifier), path(output_manifest), val(cleanup)

    output:
    tuple val(identifier), val(entry_ids), emit: completed

    script:
    entry_ids = cleanup.entries.collect { it.entry_id }
    def helper = cleanup_quote("${moduleDir}/../templates/finalization_records.py")
    def commands = cleanup.entries.collect { request ->
        def options = "--manifest ${cleanup_quote(output_manifest)} --entry-id ${cleanup_quote(request.entry_id)} --action delete_alignment --delete-source"
        def sources = request.deletion_paths.collect { cleanup_quote(it) }.join(' ')
        """
        python3 ${helper} prepare ${options}
        python3 ${helper} authorize ${options}
        rm -f -- ${sources}
        python3 ${helper} complete ${options}
        """
    }.join('\n')
    """
    set -euo pipefail
    ${commands}
    """
}
