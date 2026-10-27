"""Bounded, redacted runner observations. Worker text is evidence, never authority."""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import tempfile
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from hipson.redaction import REDACTION, redact_text

MAX_ITEMS = 20
LINE_LIMIT = 1024
READ_LIMIT = 65536
HEARTBEAT_SECONDS = 1.0


def safe_text(value: object, limit: int = LINE_LIMIT) -> str:
    text = str(value)
    if '\x00' in text or '\ufffe' in text or '\ufeff' in text:
        return '[unsupported encoded observation]'
    if len(text) > READ_LIMIT:
        return '[observation exceeded size limit]'
    secrets = {secret for key, secret in os.environ.items() if secret and re.search(
        r'(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|ACCESS_KEY_ID)(?:_|$)', key, re.I)}
    for secret in sorted(secrets, key=lambda value: (-len(value), value)):
        text = text.replace(secret, REDACTION)
    text = redact_text(text)
    # Also suppress an incomplete key block, including when bounded reads cut off its end.
    text = re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*', REDACTION, text, flags=re.DOTALL)
    return text[:limit]


def bounded_activity(value: dict[str, Any]) -> dict[str, Any]:
    observations = value.get('observations', [])[-MAX_ITEMS:]
    return {
        'heartbeat_at': safe_text(value.get('heartbeat_at', '')),
        'last_stdout': safe_text(value.get('last_stdout', '')),
        'last_stderr': safe_text(value.get('last_stderr', '')),
        'progress': safe_text(value.get('progress', '')),
        'observations': [
            {'timestamp': safe_text(item.get('timestamp', '')), 'source': safe_text(item.get('source', '')),
             'text': safe_text(item.get('text', ''))} for item in observations
        ],
        'untrusted_fields': ['last_stdout', 'last_stderr', 'progress', 'observations'],
    }


def bounded_journal(value: dict[str, Any]) -> dict[str, Any]:
    return {
        'result_state': value.get('result_state') if value.get('result_state') in ('none', 'rejected', 'valid') else 'none',
        'exit_code': value.get('exit_code'), 'signal': value.get('signal'),
        'duration_seconds': value.get('duration_seconds'),
        'changed_files': [safe_text(item) for item in value.get('changed_files', [])[:100]],
        'diff_stat': safe_text(value.get('diff_stat', ''), 8192),
        'log_tail': bounded_activity({'observations': value.get('log_tail', [])})['observations'],
        'reason': safe_text(value.get('reason', '')),
        'uncertainty': safe_text(value.get('uncertainty', '')),
    }


class Lines:
    """Consume complete lines with bounded memory, suppressing multiline private keys."""

    def __init__(self) -> None:
        self.pending = b''
        self.oversize = False
        self.private = False
        self.unsupported = False
        self.decoder = codecs.getincrementaldecoder('utf-8')()

    def feed(self, chunk: bytes, *, final: bool = False) -> list[str]:
        if self.unsupported:
            return []
        # Observations support UTF-8 only. Reject UTF-16/32 and binary streams before
        # byte-line splitting can disguise credentials/private-key boundaries with NULs.
        data = self.pending + chunk
        try:
            self.decoder.decode(chunk, final=final)
            unsupported = b'\x00' in data
        except UnicodeDecodeError:
            unsupported = True
        if unsupported:
            self.pending = b''
            self.unsupported = True
            return ['[unsupported encoded observation]']
        parts = data.split(b'\n')
        self.pending = parts.pop()
        if final and (self.pending or self.oversize):
            parts.append(self.pending)
            self.pending = b''
        lines = []
        for part in parts:
            text = part.decode('utf-8', errors='replace').rstrip('\r')
            private = self.private or bool(re.search(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', text))
            self.private = private and not bool(re.search(r'-----END [A-Z ]*PRIVATE KEY-----', text))
            lines.append(REDACTION if private else '[line exceeded size limit]'
                         if self.oversize or len(part) > 8192 else text)
            self.oversize = False
        if len(self.pending) > 8192:
            # Detect key starts before discarding an oversized incomplete line.
            if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', self.pending):
                self.private = True
            self.pending = self.pending[-128:]
            self.oversize = True
        return lines


class Activity:
    def __init__(self, progress: IO[bytes]):
        self.progress_file = progress  # Keep the original descriptor; never follow worker-replaced paths.
        self.progress_lines = Lines()
        self.lock = threading.Lock()
        self.observations: deque[dict[str, str]] = deque(maxlen=MAX_ITEMS)
        self.last = {'stdout': '', 'stderr': '', 'progress': ''}
        self.threads: list[threading.Thread] = []

    def record(self, source: str, text: str) -> None:
        with self.lock:
            self.last[source] = text
            self.observations.append({'timestamp': datetime.now(UTC).isoformat(), 'source': source, 'text': text})

    def relay(self, pipe: IO[bytes], source: str, target: int) -> None:
        def consume() -> None:
            lines = Lines()
            try:
                while chunk := os.read(pipe.fileno(), READ_LIMIT):
                    for line in lines.feed(chunk):
                        self.record(source, line)
                    try:
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(target, view):]
                    except OSError:
                        pass  # Continue draining even if the lead's output consumer closes.
                for line in lines.feed(b'', final=True):
                    self.record(source, line)
            except OSError:
                pass
            finally:
                pipe.close()
        thread = threading.Thread(target=consume, daemon=True)
        self.threads.append(thread)
        thread.start()

    def snapshot(self, *, final: bool = False) -> dict[str, Any]:
        remaining = READ_LIMIT
        if final:
            position = self.progress_file.tell()
            remaining = max(0, self.progress_file.seek(0, os.SEEK_END) - position)
            self.progress_file.seek(position)
        # Finalization drains through the observed EOF in bounded batches, retaining
        # parser/key state and only the last MAX_ITEMS. Later appends cannot prolong it.
        while remaining:
            chunk = self.progress_file.read(min(READ_LIMIT, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            for line in self.progress_lines.feed(chunk)[-MAX_ITEMS:]:
                self.record('progress', line)
        if final:
            for line in self.progress_lines.feed(b'', final=True):
                self.record('progress', line)
        with self.lock:
            return bounded_activity({'heartbeat_at': datetime.now(UTC).isoformat(),
                                     'last_stdout': self.last['stdout'], 'last_stderr': self.last['stderr'],
                                     'progress': self.last['progress'], 'observations': list(self.observations)})

    def join(self) -> None:
        for thread in self.threads:
            thread.join(timeout=2)


def project_changes(project: str) -> dict[str, Any]:
    """Observe the task project, ignoring inherited Git overrides; never attribute changes to a worker."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    outputs = []
    try:
        for command in (['status', '--porcelain=v1', '--untracked-files=normal'],
                        ['diff', '--no-ext-diff', '--no-textconv', '--stat'],
                        ['diff', '--no-ext-diff', '--no-textconv', '--stat', '--cached']):
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                         '-C', str(Path(project).resolve()), *command], env=env,
                                        stdout=output, stderr=subprocess.DEVNULL, timeout=5, check=False)
                output.seek(0)
                text = output.read(READ_LIMIT).decode('utf-8', errors='replace')
                outputs.append(text if result.returncode == 0 else '[git observation unavailable]')
    except (OSError, subprocess.SubprocessError):
        outputs = ['[git observation unavailable]', '']
    return {'changed_files': outputs[0].splitlines()[:100], 'diff_stat': '\n'.join(outputs[1:]),
            'uncertainty': 'Project snapshot may include pre-existing or concurrent changes; it is not worker attribution.'}
