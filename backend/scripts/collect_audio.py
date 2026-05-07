"""
collect_audio.py — Build elderly Indian speech corpus from YouTube + local recordings.

Usage:
  # Download YouTube audio (Telugu/Hindi elderly speech, public datasets)
  python scripts/collect_audio.py youtube \
    --urls_file scripts/youtube_urls.txt \
    --output_dir data/corpus/raw

  # Segment long recordings into 5-30s clips
  python scripts/collect_audio.py segment \
    --input_dir data/corpus/raw \
    --output_dir data/corpus/segmented

  # Transcribe segments with base Whisper (bootstrap transcripts for review)
  python scripts/collect_audio.py transcribe \
    --input_dir data/corpus/segmented \
    --output_dir data/corpus/transcribed

  # Split into train/eval (80/20)
  python scripts/collect_audio.py split \
    --input_dir data/corpus/transcribed \
    --output_dir data/corpus/final
"""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# YouTube download
# ---------------------------------------------------------------------------

def download_youtube(urls_file: str, output_dir: str):
    """Download audio from YouTube URLs using yt-dlp."""
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit("pip install yt-dlp")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(urls_file) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"Downloading {len(urls)} URLs → {out}")

    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
        "outtmpl": str(out / "%(title)s.%(ext)s"),
        "quiet": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            try:
                ydl.download([url])
            except Exception as e:
                print(f"Failed: {url} — {e}")

    print(f"Downloaded to {out}")


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def segment_audio(input_dir: str, output_dir: str, min_sec: float = 5.0, max_sec: float = 30.0):
    """
    Split long audio files into VAD-based segments using silero-vad.
    Falls back to fixed-length chunking if VAD unavailable.
    """
    try:
        import torch
        vad_model, vad_utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
        )
        (get_speech_timestamps, _, read_audio, *_) = vad_utils
        use_vad = True
    except Exception:
        print("silero-vad unavailable, using fixed 15s chunking")
        use_vad = False

    try:
        import soundfile as sf
        import numpy as np
    except ImportError:
        raise SystemExit("pip install soundfile numpy")

    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    wav_files = list(inp.rglob("*.wav")) + list(inp.rglob("*.mp3"))
    print(f"Segmenting {len(wav_files)} files...")

    counter = 0
    for wav_path in wav_files:
        try:
            audio, sr = sf.read(str(wav_path))
            if audio.ndim > 1:
                audio = audio.mean(axis=1)

            # Resample to 16kHz if needed
            if sr != 16000:
                try:
                    import resampy
                    audio = resampy.resample(audio, sr, 16000)
                    sr = 16000
                except ImportError:
                    print(f"  Skip resampling {wav_path.name} (pip install resampy)")

            if use_vad:
                import torch
                audio_tensor = torch.FloatTensor(audio)
                timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=sr)
                segments = []
                for ts in timestamps:
                    start = ts["start"] / sr
                    end = ts["end"] / sr
                    duration = end - start
                    if min_sec <= duration <= max_sec:
                        segments.append((ts["start"], ts["end"]))
                    elif duration > max_sec:
                        # split long segment into max_sec chunks
                        cur = ts["start"]
                        while cur < ts["end"]:
                            end_sample = min(cur + int(max_sec * sr), ts["end"])
                            if (end_sample - cur) / sr >= min_sec:
                                segments.append((cur, end_sample))
                            cur = end_sample
            else:
                chunk_samples = int(15 * sr)
                segments = [
                    (i, min(i + chunk_samples, len(audio)))
                    for i in range(0, len(audio), chunk_samples)
                    if (min(i + chunk_samples, len(audio)) - i) / sr >= min_sec
                ]

            for start_s, end_s in segments:
                segment = audio[start_s:end_s]
                out_path = out / f"seg_{counter:05d}.wav"
                sf.write(str(out_path), segment, sr)
                counter += 1

        except Exception as e:
            print(f"  Error {wav_path.name}: {e}")

    print(f"Created {counter} segments in {out}")


# ---------------------------------------------------------------------------
# Transcription (bootstrap)
# ---------------------------------------------------------------------------

def transcribe_segments(input_dir: str, output_dir: str, language: str = "hi"):
    """Transcribe segments with base Whisper to create initial transcript files."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("pip install faster-whisper")

    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading Whisper base model for bootstrap transcription...")
    model = WhisperModel("base", device="cuda" if _cuda_available() else "cpu", compute_type="int8")

    wav_files = sorted(inp.glob("*.wav"))
    print(f"Transcribing {len(wav_files)} segments...")

    for i, wav_path in enumerate(wav_files):
        segments, info = model.transcribe(str(wav_path), language=language, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments)

        # copy audio
        dst_wav = out / wav_path.name
        shutil.copy2(wav_path, dst_wav)

        # write transcript
        (out / wav_path.stem).with_suffix(".txt").write_text(text, encoding="utf-8")

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(wav_files)}")

    print(f"Transcribed {len(wav_files)} files → {out}")
    print("\nMANUAL REVIEW REQUIRED:")
    print("  Open .txt files alongside audio, fix errors.")
    print("  Focus on: medication names, family names, location names.")


# ---------------------------------------------------------------------------
# Train/eval split
# ---------------------------------------------------------------------------

def split_dataset(input_dir: str, output_dir: str, eval_ratio: float = 0.2, seed: int = 42):
    inp = Path(input_dir)
    out = Path(output_dir)

    wav_files = sorted(inp.glob("*.wav"))
    txt_files = {p.stem: p for p in inp.glob("*.txt")}

    # Only keep files that have matching transcripts
    paired = [(w, txt_files[w.stem]) for w in wav_files if w.stem in txt_files]
    print(f"Paired files: {len(paired)}")

    random.seed(seed)
    random.shuffle(paired)

    n_eval = int(len(paired) * eval_ratio)
    eval_pairs = paired[:n_eval]
    train_pairs = paired[n_eval:]

    for split, pairs in [("train", train_pairs), ("eval", eval_pairs)]:
        split_dir = out / split
        split_dir.mkdir(parents=True, exist_ok=True)
        for wav, txt in pairs:
            shutil.copy2(wav, split_dir / wav.name)
            shutil.copy2(txt, split_dir / txt.name)
        print(f"{split}: {len(pairs)} files → {split_dir}")

    total_hours = sum(
        _get_duration(p[0]) for p in paired if _get_duration(p[0]) > 0
    ) / 3600
    print(f"\nTotal corpus: ~{total_hours:.1f} hours")


def _get_duration(wav_path: Path) -> float:
    try:
        import soundfile as sf
        info = sf.info(str(wav_path))
        return info.duration
    except Exception:
        return 0.0


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build Sahayak speech corpus")
    sub = parser.add_subparsers(dest="cmd")

    yt = sub.add_parser("youtube", help="Download audio from YouTube URLs")
    yt.add_argument("--urls_file", required=True, help="Text file with one URL per line")
    yt.add_argument("--output_dir", default="data/corpus/raw")

    seg = sub.add_parser("segment", help="Segment long audio into clips via VAD")
    seg.add_argument("--input_dir", required=True)
    seg.add_argument("--output_dir", default="data/corpus/segmented")
    seg.add_argument("--min_sec", type=float, default=5.0)
    seg.add_argument("--max_sec", type=float, default=30.0)

    tr = sub.add_parser("transcribe", help="Bootstrap transcripts with base Whisper")
    tr.add_argument("--input_dir", required=True)
    tr.add_argument("--output_dir", default="data/corpus/transcribed")
    tr.add_argument("--language", default="hi")

    sp = sub.add_parser("split", help="Split into train/eval")
    sp.add_argument("--input_dir", required=True)
    sp.add_argument("--output_dir", default="data/corpus/final")
    sp.add_argument("--eval_ratio", type=float, default=0.2)

    args = parser.parse_args()

    if args.cmd == "youtube":
        download_youtube(args.urls_file, args.output_dir)
    elif args.cmd == "segment":
        segment_audio(args.input_dir, args.output_dir, args.min_sec, args.max_sec)
    elif args.cmd == "transcribe":
        transcribe_segments(args.input_dir, args.output_dir, args.language)
    elif args.cmd == "split":
        split_dataset(args.input_dir, args.output_dir, args.eval_ratio)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
