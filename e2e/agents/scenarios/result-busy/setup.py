"""Two tasks: a pending parser delivery and independent work occupying the lead."""


def setup(fixture):
    task = fixture.open_task('Consume a result while busy')
    other = fixture.open_task('Independent lead work')
    (fixture.workdir / 'bin/delayed-worker.py').write_text(
        "import subprocess, time\nfrom pathlib import Path\n"
        "Path('.worker-ready').touch()\n"
        "deadline = time.monotonic() + 10\n"
        "while not Path('.lead-busy').exists():\n"
        "    if time.monotonic() > deadline: raise SystemExit(8)\n"
        "    time.sleep(0.02)\n"
        "subprocess.run(['bin/fake-worker', 'build', 'delivered.json'], check=True)\n")
    return {'task': task, 'other': other}
