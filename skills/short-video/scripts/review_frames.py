#!/usr/bin/env python3
"""Extract every decoded video frame and contact sheets; never certify visual review."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def positive(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return n


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('video', type=Path)
    p.add_argument('--output', required=True, type=Path, help='new output directory')
    p.add_argument('--columns', type=positive, default=6)
    p.add_argument('--rows', type=positive, default=4)
    p.add_argument('--width', type=positive, default=240, help='thumbnail width, even pixels')
    a = p.parse_args()
    if not a.video.is_file():
        p.error('input video does not exist')
    if a.output.exists():
        p.error('output already exists; select a new directory to preserve earlier evidence')
    if a.width < 80 or a.width % 2:
        p.error('width must be an even number >= 80')
    source = a.video.resolve()
    probe = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                           '-count_frames', '-show_streams', '-show_frames', '-show_entries',
                           'stream=width,height,avg_frame_rate,nb_read_frames:frame=best_effort_timestamp_time',
                           '-of', 'json', str(source)]))
    streams = probe.get('streams', [])
    frames = probe.get('frames', [])
    if not streams or not frames:
        raise ValueError('no decodable video frames')
    count = int(streams[0]['nb_read_frames'])
    if count != len(frames):
        raise ValueError('probe frame count mismatch')
    a.output.mkdir(parents=True, exist_ok=False)
    extracted = a.output / 'frames'
    extracted.mkdir()
    labels = "drawtext=text='f%{n} %{pts\\:hms}':fontsize=14:fontcolor=yellow:box=1:boxcolor=black@0.6:x=3:y=3"
    filters = run(['ffmpeg', '-hide_banner', '-filters'])
    embedded_labels = any(' drawtext ' in line for line in filters.splitlines())
    frame_filter = f'scale={a.width}:-2' + (f',{labels}' if embedded_labels else '')
    run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source), '-map', '0:v:0',
         '-an', '-sn', '-dn', '-vf', frame_filter, '-fps_mode', 'passthrough',
         '-start_number', '0', '-q:v', '3', str(extracted/'frame_%08d.jpg')])
    pictures = sorted(extracted.glob('frame_*.jpg'))
    if len(pictures) != count:
        raise ValueError(f'extracted {len(pictures)} frames; expected {count}; incomplete evidence retained')
    run(['ffmpeg', '-v', 'error', '-nostdin', '-framerate', '1', '-start_number', '0',
         '-i', str(extracted/'frame_%08d.jpg'), '-vf',
         f'tile={a.columns}x{a.rows}:padding=2:color=gray', '-fps_mode', 'passthrough',
         '-start_number', '0', '-q:v', '3', str(a.output/'sheet_%04d.jpg')])
    pages = sorted(a.output.glob('sheet_*.jpg'))
    capacity = a.columns*a.rows
    if len(pages) != (count+capacity-1)//capacity:
        raise ValueError('contact sheet count mismatch; incomplete evidence retained')
    digest = hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            digest.update(chunk)
    data = {
        'source': str(source), 'source_sha256': digest.hexdigest(),
        'source_frame_count': count, 'extracted_frame_count': len(pictures),
        'stream': streams[0], 'columns': a.columns, 'rows': a.rows,
        'embedded_labels': embedded_labels, 'cell_order': 'row-major, zero-based frame indices',
        'visual_review_complete': False,
        'review_note': 'Extraction is complete. Inspect every sheet and record findings separately; enlarge suspect frames.',
        'sheets': [{'file': page.name, 'first_frame': i*capacity,
                    'last_frame': min(count-1,(i+1)*capacity-1)} for i,page in enumerate(pages)],
        'frames': [{'frame': i, 'time_seconds': float(f['best_effort_timestamp_time'])
                    if 'best_effort_timestamp_time' in f else None,
                    'file': str(pic.relative_to(a.output))} for i,(f,pic) in enumerate(zip(frames,pictures))]
    }
    (a.output/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'frames':count,'sheets':len(pages),'manifest':str(a.output/'manifest.json'),
                      'visual_review_complete':False},ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'error: {exc}',file=sys.stderr)
        if isinstance(exc,subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr,file=sys.stderr)
        sys.exit(1)
