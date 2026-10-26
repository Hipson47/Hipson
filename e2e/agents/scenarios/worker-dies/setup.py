"""A worker that leaves partial changes and exits without returning evidence."""


def setup(fixture):
    task = fixture.open_task('Recover worker without a result')
    (fixture.workdir / 'bin/crash-worker.py').write_text(
        "import os, sys\nfrom pathlib import Path\n"
        "Path('src/parser/partial.py').write_text('# partial work\\n')\n"
        "with open(os.environ['HIPSON_PROGRESS_FILE'], 'a') as progress:\n"
        "    progress.write('Inspected parser; implementation incomplete\\n')\n"
        "print('worker stopped before result', file=sys.stderr)\nraise SystemExit(9)\n")
    return {'task': task}
