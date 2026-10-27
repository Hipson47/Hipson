"""A consumed context is lost, while delivery, a finding and live ownership remain."""

import subprocess


def setup(fixture):
    task = fixture.cli(
        'task', 'open', '--project', '.', '--title', 'Finish parser whitespace repair',
        '--objective', 'Fix colon-pair whitespace with bin/fake-worker fix RESULT.json via hipson task run '
        '(builder, rw, scope src/parser, matching --result-file). Independently verify the parser.',
        '--acceptance', 'Read and acknowledge the previous delivery; fix and resolve the major finding.',
        '--acceptance', 'Leave task in_progress for later review; preserve the live holder on src/held.',
        '--lead', 'lead',
    )['id']
    subprocess.run([str(fixture.workdir / 'bin/hipson'), 'task', 'run', '--task', task,
                    '--actor', 'previous-builder', '--role', 'builder', '--mode', 'rw', '--scope', 'src/parser',
                    '--result-file', 'previous.json', '--', 'bin/fake-worker', 'build', 'previous.json'],
                   cwd=fixture.workdir, env=fixture.env, capture_output=True, text=True, check=True, timeout=15)
    finding = fixture.cli('task', 'finding', 'add', '--task', task, '--actor', 'reviewer',
                          '--severity', 'major', '--title', 'Pair elements retain surrounding whitespace',
                          '--detail', "Trim each element: parsing ' a : b ' must return ('a', 'b').",
                          '--location', 'src/parser/pairs.py:2', '--source-assignment', 'A-0001')
    other = fixture.open_task('Keep the existing worker on src/held running; do not replace or cancel it')
    holder = fixture.start_holder(other, scope='src/held')
    fixture.cli('task', 'event', '--task', task, '--actor', 'lead', '--type', 'decision',
                '--summary', 'Next: consume A-0001, repair F-0001 on src/parser; src/held remains owned.')
    resume = subprocess.run([str(fixture.workdir / 'bin/hipson'), 'lead', 'resume'], cwd=fixture.workdir,
                            env=fixture.env, capture_output=True, text=True, check=True, timeout=15).stdout
    return {'task': task, 'other': other, 'holder': holder, 'delivered': 'A-0001',
            'finding': finding['id'], 'resume_output': resume}
