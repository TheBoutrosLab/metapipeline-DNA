"""Check that failed children/submissions remain failed, ignored tasks with no handoff.

Usage: python3 test_child_launcher.py [nextflow launcher command ...]
"""
from pathlib import Path
import json, subprocess, sys, tempfile


def main():
    repo=Path(__file__).resolve().parents[2]
    source=(repo/'main.nf').read_text()
    quote=source[source.index('def shellQuote('):source.index('// Log info here')]
    process=source[source.index('process call_metapipeline_DNA {'):source.index('process check_process_status {')]
    import re
    for mode in [False, True]:
        with tempfile.TemporaryDirectory(prefix='child-launch-test-') as temp:
            root=Path(temp)
            (root/'templates').mkdir()
            wrapper=root/'templates/nextflow-wrapper'
            wrapper.write_text('''#!/usr/bin/env python3
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--completion_report')
p.add_argument('--task_hash')
p.add_argument('--workflow_identifier')
a,_=p.parse_known_args()
Path(a.completion_report).write_text(json.dumps(dict(run_id=a.task_hash,workflow_identifier=a.workflow_identifier,exit_code=17)))
raise SystemExit(17)
''')
            wrapper.chmod(0o755)
            params={key:'' for key in set(re.findall(r'params\.([a-zA-Z_0-9]+)',process))}
            params.update(sample_counts={'P': {'normal': 1, 'tumor': 1}}, uclahs_cds_wgs=mode,log_output_dir=str(root/'logs'),resolved_work_dir=str(root/'scratch'),
                          global_job_submission_limiter='CURRENT_WORK_DIR=`pwd`\nFIRST_DIR_HASH=aa\nSECOND_DIR_HASH=bb\n',
                          global_job_submission_sbatch='SBATCH_RET=$(false ')
            (root/'params.json').write_text(json.dumps(params))
            (root/'input.json').write_text('{}')
            script=root/'test.nf'
            script.write_text('''nextflow.enable.dsl = 2
    import groovy.json.JsonSlurper
    params.putAll(new JsonSlurper().parse(new File("${projectDir}/params.json")))
    '''+quote+process+'''
    workflow {
        call_metapipeline_DNA(Channel.of(['P', file("${projectDir}/input.json"), 'sample']))
        call_metapipeline_DNA.out.submit_out.toList().view { results ->
            assert results == []
            'Failed child/submission emits no handoff; other workflows can continue'
        }
    }
    ''')
            config = root / 'nextflow.config'
            config.write_text("process.executor = 'local'\nprocess { withName:call_metapipeline_DNA { errorStrategy = 'ignore' } }\n")
            command=(sys.argv[1:] or ['nextflow']) + ['-C',str(config),'run',str(script),'-ansi-log','false']
            p=subprocess.run(command,cwd=root)
            if p.returncode:
                for path in root.glob('work/*/*/.command.sh'): print(path.read_text())
                raise SystemExit(p.returncode)
            exits = [p.read_text().strip() for p in root.glob('work/*/*/.exitcode')]
            assert len(exits) == 1 and exits[0] != '0', exits
            if not mode:
                assert exits == ['17'], exits
            assert 'Error is ignored' in (root / '.nextflow.log').read_text()
            print('Original failure/ignore behavior verified')


if __name__ == "__main__":
    main()
