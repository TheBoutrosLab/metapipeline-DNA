import org.yaml.snakeyaml.Yaml
/*
* Create input YAML file for the call-mtSNV pipeline.
*
* Input:
*   sample_info: A Map object containing sample information split into normal and tumor
*
* Output:
*   @return A tuple of 2 items, inlcuding the patient_id and input_yaml
*/
process create_YAML_call_mtSNV {
    publishDir "${params.output_dir}/intermediate/${task.process.replace(':', '/')}-${params.patient}/${mtsnv_sample_id}",
        pattern: 'call_mtSNV_input.yaml',
        mode: 'copy'

    input:
        val(sample_info)

    output:
        tuple val(mtsnv_sample_id), path(input_yaml), emit: call_mtsnv_yaml

    exec:
    input_yaml = 'call_mtSNV_input.yaml'

    single_sample_type = 'none'
    if (sample_info.tumor.isEmpty()) {
        single_sample_type = 'normal'
    } else {
        single_sample_type = 'tumor'
    }

    mtsnv_sample_id = ''
    if (params.sample_mode == 'single') {
        if (!(sample_info[single_sample_type].sample.size() == 1)) {
            throw new Exception("Single sample expected but received: `${sample_info[single_sample_type].sample.size()}`");
        }
        mtsnv_sample_id = sample_info[single_sample_type].sample[0]
    } else {
        if (!(sample_info.tumor.sample.size() == 1)) {
            throw new Exception("Single tumor sample expected but received: `${sample_info.tumor.sample.size()}`");
        }
        mtsnv_sample_id = sample_info.tumor.sample[0]
    }

    if (params.sample_mode == 'single') {
        input_map = [
            'input': [
                'BAM': [
                    ("${single_sample_type}" as String): ['path': sample_info[single_sample_type]['bam'][0] as String, 'sample_id': mtsnv_sample_id]
                ]
            ]
        ]
    } else {
        input_map = [
            'input': [
                'BAM': [
                    'normal': ['path': sample_info.normal.bam[0] as String, 'sample_id': sample_info.normal.sample[0] as String],
                    'tumor': ['path': sample_info.tumor.bam[0] as String, 'sample_id': sample_info.tumor.sample[0] as String]
                ]
            ]
        ]
    }
    Yaml yaml = new Yaml()
    yaml.dump(input_map, new FileWriter("${task.workDir}/${input_yaml}"))
}
