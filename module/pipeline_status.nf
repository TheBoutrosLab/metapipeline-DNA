import groovy.json.JsonOutput

/** Declare the exit records required to establish downstream success. */
def write_expected_pipeline_statuses() {
    def expected = ['generate-SQC-BAM', 'call-gSNP', 'call-gSV', 'call-sSV', 'call-sSNV', 'call-sCNA']
    ['call_mtSNV', 'call_SRC', 'StableLift', 'annotate_VCF', 'call_GeneticAncestry', 'calculate_mtDNA_CopyNumber'].each { key ->
        if (params[key].is_pipeline_enabled) {
            expected.add(key.replace('_', '-'))
        }
    }
    if (params.calculate_targeted_coverage.is_pipeline_enabled && params.use_original_intervals) {
        expected.add('calculate-targeted-coverage')
    }
    new File("${params.pipeline_exit_status_directory}/expected_pipelines.json").text = JsonOutput.toJson(expected.sort()) + '\n'
}

/*
*   Create the given directory and any necessary parent directories
*/
def create_directory(String directory_to_create) {
    new File(directory_to_create).mkdirs()
}

/*
*   Mark a pipeline as complete in directory
*   @input pipeline String Name of pipeline to mark as complete
*/
def mark_pipeline_complete(String pipeline) {
    params.pipeline_predecessor.each{ p, dependencies ->
        dependencies.removeAll{ it == pipeline };
        if (dependencies.isEmpty()) {
            File pipeline_ready_file = new File("${params.pipeline_status_directory}/${p}.ready")
            pipeline_ready_file.delete()
            pipeline_ready_file.createNewFile()
        }
    }
}

/*
*   Delete a completion file for testing purposes
*   @input pipeline String Name of pipeline to delete completion status
*/
def delete_completion_file(String pipeline) {
    new File("${params.pipeline_status_directory}/${pipeline}.ready").delete()
}

/*
*   Create a file to record the exit code of a pipeline
*   @input pipeline String Name of pipeline to mark as complete
*   @input exit_code Integer Exit code of pipeline
*/
def mark_pipeline_exit_code(String pipeline, Integer exit_code) {
    new File("${params.pipeline_exit_status_directory}/${pipeline}.${exit_code}").createNewFile()
}
