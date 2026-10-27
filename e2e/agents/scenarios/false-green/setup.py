"""A zero-exit builder reports completion; independent checks decide acceptance."""


def setup(fixture):
    tests = fixture.workdir / 'tests'
    tests.mkdir()
    check = "import sys\nsys.path.insert(0, '.')\nfrom src.parser.pairs import parse_pair\nassert parse_pair(' a : b ') == ('a', 'b')\n"
    (tests / 'acceptance.py').write_text(check)
    worker = fixture.workdir / 'bin' / 'candidate.py'
    worker.write_text("import json\nfrom pathlib import Path\n"
                      "Path('candidate.json').write_text(json.dumps({'status': 'completed', 'summary': 'All tests pass', "
                      "'files_changed': [], 'commands': [], 'findings': [], 'risks': []}))\n")
    task = fixture.open_task()
    return {'task': task, 'tests': check}
