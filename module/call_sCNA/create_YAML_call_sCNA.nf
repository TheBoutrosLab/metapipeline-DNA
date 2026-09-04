import org.yaml.snakeyaml.Yaml
/*
* Create input YAML file for the call-sCNA pipeline.
*
* Input:
*   sample_info: A Map object containing sample information split into normal and tumor
*
* Output:
*   @return A path to the input YAML
*/
process create_YAML_call_sCNA {

    publishDir "${params.output_dir}/intermediate/${task.process.replace(':', '/')}-${tumor_id}",
        enabled: params.save_intermediate_files,
        pattern: 'call_sCNA_input.yaml',
        mode: 'copy'

    input:
        tuple(
            val(tumor_id), val(genetic_sex), val(normal_bam), val(tumor_bam)
        )

    output:
        tuple val(tumor_id), path(input_yaml)

    exec:
    input_yaml = 'call_sCNA_input.yaml'


    /**
        Default to XY if Battenberg is not being run to avoid errors with call-sCNA requiring the genetic_sex param
        and only accepting XY or XX.
        This avoids validation from failing with unknown genetic_sex when only FACETS is requested
    */
    boolean battenberg_enabled = params.call_sCNA.metapipeline_arg_map.algorithm.contains('Battenberg')
    String resolved_sex = genetic_sex
    if (!battenberg_enabled && !(genetic_sex in ['XX', 'XY'])) {
        resolved_sex = 'XY'
    }

    input_map = [
            'patient_id': tumor_id,
            'genetic_sex': resolved_sex,
            'input': [
                'BAM': [
                    'normal': [ "${normal_bam}" as String ],
                    'tumor': [ "${tumor_bam}" as String ]
                ]
            ]
        ]
    Yaml yaml = new Yaml()
    yaml.dump(input_map, new FileWriter("${task.workDir}/${input_yaml}"))
}
