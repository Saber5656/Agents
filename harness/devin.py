"""Adapt native Devin print/ATIF output to the runner's recorded JSON stream.

Invoked by absolute script path so it works from arbitrary task workspaces.
Authentication remains with Devin; temporary files contain no credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MODELS = ('swe-2-medium', 'swe-2-high', 'swe-2-max')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, default='swe-2-medium')
    args = parser.parse_args(argv)
    # Native exports may contain private context. Keep the transient original
    # private; only the runner's redaction collector persists the JSON event.
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='agents-devin-') as directory:
        root = Path(directory)
        prompt = root / 'prompt.md'
        export = root / 'trajectory.json'
        config = root / 'config.json'
        prompt.write_text('The OS sandbox applies to exec. Perform all workspace edits and tests via exec '
                          '(for example, python3); do not use the edit/write tools. '
                          'Do not invoke skills, hooks, plugins, MCP or additional agents.\n\n' + sys.stdin.read())
        config.write_text(json.dumps({
            'subagents_enabled': False,
            'auto_update': False,
            'read_config_from': {'claude': False, 'cursor': False, 'windsurf': False},
            'permissions': {'deny': ['edit', 'write', 'run_subagent', 'read_subagent', 'mcp__*',
                                     'browser_preview', 'close_browser_preview']},
        }))
        command = ['devin', '--model', args.model, '--config', str(config),
                   '--sandbox',
                   '--respect-workspace-trust', 'false',
                   '--prompt-file', str(prompt), '--export', str(export), '-p']
        # Keep native text out of the JSON control channel. The parent records
        # it live as stderr and owns the process-group timeout for both processes.
        try:
            process = subprocess.run(command, stdin=subprocess.DEVNULL,
                                     stdout=sys.stderr, stderr=sys.stderr)
        except OSError as exc:
            print(f'Devin startup failed: {exc}', file=sys.stderr)
            return 127 if isinstance(exc, FileNotFoundError) else 126
        trajectory = None
        try:
            trajectory = json.loads(export.read_text())
        except (OSError, ValueError):
            pass
        print(json.dumps({'type': 'devin_result', 'exit_code': process.returncode,
                          'trajectory': trajectory}, ensure_ascii=False), flush=True)
        return process.returncode if process.returncode >= 0 else 128 - process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
