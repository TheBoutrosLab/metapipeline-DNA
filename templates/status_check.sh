#!/bin/bash

# These template values already include shell quoting.
work_dir=!{quoted_work_dir}
sbatch_ret=!{quoted_sbatch_ret}
identifier=!{quoted_identifier}

if [ "$sbatch_ret" != "-1" ]
then
    if printf '%s\n' "$sbatch_ret" | grep -q "Submitted batch job"
    then
        job_id=$(printf '%s\n' "$sbatch_ret" | cut -d ' ' -f 4)
        job_queue=$(squeue --noheader --format="%i" || echo "failed")

        while [[ "$job_queue" == "failed" ]] || echo "$job_queue" | grep "^$job_id$" &> /dev/null
        do
            sleep 3
            job_queue=$(squeue --noheader --format="%i" || echo "failed")
        done

        if sacct -j "$job_id" -o ExitCode --noheader | tr -d " " | sort -r | head -n 1 | grep -q "^0:0$"
        then
            :
        else
            printf "Process in '%s' with ID: '%s' failed with non-zero exit code or the status could not be checked.\n" "$work_dir" "$identifier"
        fi
    fi
fi

pipeline_failures=""

exit_code_regex="^(.+)\\.([0-9]+)"
for pipeline_exit_path in "$work_dir"/PIPELINEEXITSTATUS/*
do
    pipeline_exit_file=$(basename "$pipeline_exit_path")
    if [[ $pipeline_exit_file =~ $exit_code_regex ]]
    then
        pipeline_name="${BASH_REMATCH[1]}"
        pipeline_exit_code="${BASH_REMATCH[2]}"
        if [ ! "$pipeline_exit_code" -eq 0 ]
        then
            pipeline_failures="$pipeline_name, $pipeline_failures"
        fi
    fi
done

if [ -n "$pipeline_failures" ]
then
    printf "Process in '%s' with ID: '%s' had failures in the following pipelines: %s\n" "$work_dir" "$identifier" "$pipeline_failures"
fi
