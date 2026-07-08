import subprocess
from pathlib import Path
from config import MAX_DURATION, MIN_DURATION, SILENCE_THRESH, MIN_SILENCE_MS, KEEP_SILENCE_MS, WAV_SR

def get_duration_s(path) -> float:
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def export_wav(seg, out_path: Path):
    (seg.set_frame_rate(WAV_SR)
       .set_channels(1)
       .set_sample_width(2)
       .export(str(out_path), format="wav"))


def convert_direct(src, dst) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vn", "-ar", str(WAV_SR), "-ac", "1", "-sample_fmt", "s16", str(dst)],
        capture_output=True)
    return r.returncode == 0


def split_by_time(audio, base: str, out_dir: Path, max_ms: int) -> list:
    parts, start, idx = [], 0, 1
    while start < len(audio):
        chunk = audio[start:start + max_ms]
        if len(chunk) / 1000 >= MIN_DURATION:
            p = out_dir / f"{base}_t{str(idx).zfill(2)}.wav"
            export_wav(chunk, p)
            parts.append(p)
            idx += 1
        start += max_ms
    return parts


def segment_by_silence(src, out_dir: Path, base: str) -> list:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence

    audio  = AudioSegment.from_file(str(src))
    chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_MS,
        silence_thresh=SILENCE_THRESH,
        keep_silence=KEEP_SILENCE_MS,
    ) or [audio]

    paths, idx = [], 1
    for chunk in chunks:
        dur = len(chunk) / 1000.0
        if dur < MIN_DURATION:
            continue
        if dur <= MAX_DURATION:
            p = out_dir / f"{base}_seg{str(idx).zfill(2)}.wav"
            export_wav(chunk, p)
            paths.append(p)
            idx += 1
        else:
            sub = split_by_time(chunk, f"{base}_seg{str(idx).zfill(2)}",
                                 out_dir, MAX_DURATION * 1000)
            paths.extend(sub)
            idx += max(1, len(sub))
    return paths