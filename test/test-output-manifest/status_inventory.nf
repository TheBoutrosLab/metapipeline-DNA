nextflow.enable.dsl = 2
import groovy.json.JsonSlurper
import java.nio.file.Files

workflow {
    def root = Files.createTempDirectory('status-inventory-')
    def settings = [pipeline_exit_status_directory: root.toString(), use_original_intervals: false]
    def optional = ['call_mtSNV', 'call_SRC', 'StableLift', 'annotate_VCF', 'call_GeneticAncestry', 'calculate_mtDNA_CopyNumber']
    (optional + ['calculate_targeted_coverage']).each { settings[it] = [is_pipeline_enabled: false] }
    def helper = new GroovyShell(new Binding([params: settings]))
        .parse(new File("${projectDir}/../../module/pipeline_status.nf"))
    def read_inventory = { new JsonSlurper().parse(root.resolve('expected_pipelines.json')) }
    def always = ['generate-SQC-BAM', 'call-gSNP', 'call-gSV', 'call-sSV', 'call-sSNV', 'call-sCNA']
    helper.write_expected_pipeline_statuses()
    assert read_inventory() == always.sort(false)
    optional.each { settings[it].is_pipeline_enabled = true }
    settings.calculate_targeted_coverage.is_pipeline_enabled = true
    helper.write_expected_pipeline_statuses()
    assert read_inventory() == (always + optional.collect { it.replace('_', '-') }).sort(false)
    settings.use_original_intervals = true
    helper.write_expected_pipeline_statuses()
    assert read_inventory().contains('calculate-targeted-coverage')
    assert read_inventory().size() == 13
    'Expected statuses match disabled, enabled and graceful-coverage configurations'
}
