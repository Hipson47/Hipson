"""A task declares its acceptance check; the lead closes it without weakening that check."""

CHECK = ['python3', 'tests/acceptance.py']


def setup(fixture):
    tests = fixture.workdir / 'tests'
    tests.mkdir()
    check = "import sys\nsys.path.insert(0, '.')\nfrom src.parser.pairs import parse_pair\nassert parse_pair(' a : b ') == ('a', 'b')\n"
    (tests / 'acceptance.py').write_text(check)
    worker = fixture.workdir / 'bin' / 'candidate.py'
    worker.write_text("import json\nfrom pathlib import Path\n"
                      "Path('src/parser/pairs.py').write_text(\"def parse_pair(text):\\n"
                      "    left, right = text.split(':', 1)\\n    return left.strip(), right.strip()\\n\")\n"
                      "Path('candidate.json').write_text(json.dumps({'status': 'completed', 'summary': 'Trim pair elements', "
                      "'files_changed': [{'path': 'src/parser/pairs.py', 'change': 'strip both sides'}], "
                      "'commands': [], 'findings': [], 'risks': []}))\n")
    task = fixture.cli('task', 'open', '--project', '.', '--title', 'Fix parser', '--objective', 'Trim pair elements',
                       '--check', ' '.join(CHECK), '--lead', 'lead')['id']
    return {'task': task, 'tests': check}
