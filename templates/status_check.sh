#!/bin/bash

# Values already include shell quoting. Expected failures are reported as data,
# so an unsuccessful patient/sample never aborts other patient/sample workflows.
if ! python3 !{quoted_status_checker} \
    --work-dir !{quoted_work_dir} \
    --submission !{quoted_sbatch_ret} \
    --identifier !{quoted_identifier} \
    --manifest !{quoted_output_manifest_path}
then
    rm -f output_manifest.json
    printf '%s\n' '{"status":"unknown","reason":"Status checker could not run"}' > workflow_status.json
    printf '%s\n' unknown > workflow_status.txt
fi

WORKFLOW_STATUS=$(cat workflow_status.txt)
# Allow Nextflow's appended environment-output capture to execute.
true
