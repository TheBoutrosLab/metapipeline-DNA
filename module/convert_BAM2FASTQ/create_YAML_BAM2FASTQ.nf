import org.yaml.snakeyaml.Yaml
/*
* Create input YAML file for the convert-BAM2FASTQ pipeline.
*
* Input:
*   A tuple consisting of patient_id, sample_id, sample state, the input path, and its declared BAM/CRAM type
*
* Output:
*   @return A path to the input YAML
*/
process create_YAML_convert_BAM2FASTQ {
    publishDir "${params.output_dir}/intermediate/${task.process.replace(':', '/')}-${params.patient}/${sample}",
        pattern: "convert_BAM2FASTQ_input.yaml",
        mode: "copy"

    input:
        tuple val(patient), val(sample), val(state), val(bam), val(input_type)

    output:
        tuple val(patient), val(sample), val(state), path(input_yaml), emit: convert_bam2fastq_yaml

    exec:
    input_yaml = "convert_BAM2FASTQ_input.yaml"

    input_map = [
        'patient_id': "${patient}" as String,
        'input': [
            ("${input_type}" as String): [
                ("${state}" as String): [[
                    'path': "${bam}" as String,
                    'id': "${sample}" as String
                ]]
            ]
        ]
    ]

    Yaml yaml = new Yaml()
    yaml.dump(input_map, new FileWriter("${task.workDir}/${input_yaml}"))
}
