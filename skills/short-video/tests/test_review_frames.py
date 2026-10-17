import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'review_frames.py'


class ReviewFramesTest(unittest.TestCase):
    def test_all_frames_including_last_partial_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / 'source with spaces.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=6:duration=1.2', '-frames:v', '7',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
            out = root / 'frames'
            p = subprocess.run(['python3', str(SCRIPT), str(video), '--output', str(out),
                                '--columns', '2', '--rows', '2', '--width', '120'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads((out / 'manifest.json').read_text())
            self.assertEqual(data['source_frame_count'], 7)
            self.assertEqual(data['extracted_frame_count'], 7)
            self.assertEqual(len(data['sheets']), 2)
            self.assertEqual(data['sheets'][1]['first_frame'], 4)
            self.assertEqual(data['sheets'][1]['last_frame'], 6)
            self.assertEqual([f['frame'] for f in data['frames']], list(range(7)))
            self.assertAlmostEqual(data['frames'][6]['time_seconds'], 1.0, places=3)
            for page in data['sheets']:
                self.assertTrue((out / page['file']).is_file())
            self.assertFalse(data['visual_review_complete'])
            marker = out / 'keep.txt'
            marker.write_text('preserve')
            again = subprocess.run(['python3', str(SCRIPT), str(video), '--output', str(out)],
                                   capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(marker.read_text(), 'preserve')

    def test_missing_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / 'frames'
            p = subprocess.run(['python3', str(SCRIPT), str(root/'missing.mp4'), '--output', str(out)],
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(out.exists())

    def test_invalid_grid_creates_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / 'video.mp4'
            video.write_bytes(b'not a video')
            out = root / 'frames'
            p = subprocess.run(['python3', str(SCRIPT), str(video), '--output', str(out), '--columns', '0'],
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
