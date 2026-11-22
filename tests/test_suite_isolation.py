"""Both test runners keep the suite off the real ledger and out of a Hipson worker context.

Found while dogfooding 1.8.1: a Claude Code builder run through `hipson task run` could not run the flow tests,
because its HIPSON_ROLE=worker made lead verification refuse, and scripts/run_tests.py (what CI and `task verify`
checks run) did not isolate HIPSON_LEDGER, so a test forgetting --db would have written the builder's real ledger.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from hipson import ledger
from tests.support import ROOT, WORKER_CONTEXT_VARIABLES, isolate_test_environment


def test_the_running_suite_has_no_worker_context_and_no_real_ledger():
    for name in WORKER_CONTEXT_VARIABLES:
        assert name not in os.environ, name
    real = (Path.home() / ".config" / "hipson" / "ledger.sqlite").resolve()
    assert Path(os.environ["HIPSON_LEDGER"]).resolve() != real
    assert ledger.default_ledger_db().resolve() != real
    assert Path(os.environ["HIPSON_HOME"]).name == "home"


def test_isolation_clears_every_worker_variable_and_points_away_from_the_ledger(tmp_path):
    worker = {name: "inherited" for name in WORKER_CONTEXT_VARIABLES}
    with patch.dict(os.environ, {**worker, "HIPSON_LEDGER": "/real/ledger.sqlite", "HIPSON_HOME": "/real/home",
                                 "HIPSON_UNRELATED": "kept"}):
        isolate_test_environment(str(tmp_path))
        assert not any(name in os.environ for name in WORKER_CONTEXT_VARIABLES)
        assert os.environ["HIPSON_LEDGER"] == os.path.join(str(tmp_path), "ledger.sqlite")
        assert os.environ["HIPSON_HOME"] == os.path.join(str(tmp_path), "home")
        assert os.environ["HIPSON_UNRELATED"] == "kept"


def test_the_standalone_runner_isolates_before_importing_tests(tmp_path):
    # Run the runner's own entry point on a one-test module set: the environment it hands tests is isolated.
    probe = ("import os, sys\nsys.argv = ['run_tests.py']\nsys.path.insert(0, sys.argv[0] or '.')\n"
             "import runpy\nmodule = runpy.run_path(os.path.join(os.environ['ROOT'], 'scripts', 'run_tests.py'))\n"
             "module['discover_tests'] = lambda: [('probe', lambda: print('ENV', os.environ.get('HIPSON_ROLE'), "
             "os.environ.get('HIPSON_LEDGER')))]\n"
             "module['main'].__globals__['discover_tests'] = module['discover_tests']\n"
             "raise SystemExit(module['main']())\n")
    sentinel = tmp_path / "real-ledger.sqlite"
    env = {**os.environ, "ROOT": str(ROOT), "HIPSON_ROLE": "worker", "HIPSON_ASSIGNMENT": "A-9999",
           "HIPSON_LEDGER": str(sentinel)}
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    line = next(text for text in proc.stdout.splitlines() if text.startswith("ENV "))
    role, ledger_path = line.split(" ", 2)[1:]
    assert role == "None" and ledger_path != str(sentinel) and ledger_path.endswith("ledger.sqlite")
    assert not sentinel.exists()
