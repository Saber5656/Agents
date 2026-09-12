"""Per-job executor wrappers retain streaming artifacts without global mutation."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness import runner


class RecordingExecutorTests(unittest.TestCase):
    def test_opted_in_job_wrapper_receives_stream_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'; vault.mkdir()
            observed = []
            original = runner.execute
            def wrapped(argv, env, cwd, prompt, timeout, **records):
                observed.append(records)
                output = json.dumps({'type': 'result', 'subtype': 'success',
                                     'is_error': False, 'result': 'done'})
                if records:
                    records['stdout_path'].write_text(output)
                    records['stderr_path'].write_text('')
                return runner.ProcessResult(0, output)
            wrapped.records_streams = True
            job = runner.Job(root, vault, 'test', mode='run', fallback=False)
            result = runner.run_job(job, {}, executor=wrapped)
            self.assertIs(runner.execute, original)
            self.assertEqual(result['status'], 'completed')
            self.assertIn('state_path', observed[0])
            self.assertIn('redaction_env', observed[0])
            self.assertIn('done', observed[0]['stdout_path'].read_text())


if __name__ == '__main__':
    unittest.main()
