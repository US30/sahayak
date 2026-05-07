"""
train_whisper.py — Fine-tune Whisper-small on elderly Indian speech corpus.

Run on H100 server:
  python scripts/train_whisper.py \
    --data_dir /path/to/audio_dataset \
    --output_dir ./whisper-sahayak \
    --language hi \
    --num_train_epochs 5

Dataset directory structure expected:
  data_dir/
    train/
      audio_001.wav   (16kHz, mono, WAV)
      audio_001.txt   (transcript, same stem)
      audio_002.wav
      audio_002.txt
      ...
    eval/
      audio_101.wav
      audio_101.txt
      ...

After training, convert to faster-whisper (CTranslate2) format:
  python scripts/train_whisper.py --convert_only \
    --hf_model_dir ./whisper-sahayak \
    --output_dir ./whisper-sahayak-ct2

Copy ./whisper-sahayak-ct2/ to Mac, then set in .env:
  WHISPER_MODEL=./whisper-sahayak-ct2
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import torch


def _check_deps():
    try:
        import datasets
        import evaluate
        import transformers
    except ImportError:
        raise SystemExit(
            "Install training deps:\n"
            "  pip install transformers datasets evaluate accelerate jiwer"
        )


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------

def load_dataset_from_dir(data_dir: str, split: str, processor):
    """Load audio + transcript pairs from flat directory structure."""
    import datasets
    from datasets import Audio

    audio_dir = Path(data_dir) / split
    if not audio_dir.exists():
        raise FileNotFoundError(f"Expected {audio_dir}")

    wav_files = sorted(audio_dir.glob("*.wav"))
    if not wav_files:
        raise FileNotFoundError(f"No .wav files in {audio_dir}")

    records = []
    for wav_path in wav_files:
        txt_path = wav_path.with_suffix(".txt")
        if not txt_path.exists():
            continue
        transcript = txt_path.read_text(encoding="utf-8").strip()
        records.append({"audio": str(wav_path), "sentence": transcript})

    ds = datasets.Dataset.from_list(records)
    ds = ds.cast_column("audio", Audio(sampling_rate=16_000))
    return ds


def prepare_dataset(batch, processor, language: str):
    """Feature extraction + tokenisation for one batch."""
    audio = batch["audio"]
    batch["input_features"] = processor.feature_extractor(
        audio["array"], sampling_rate=audio["sampling_rate"], return_tensors="np"
    ).input_features[0]
    batch["labels"] = processor.tokenizer(
        batch["sentence"],
        language=language,
        task="transcribe",
    ).input_ids
    return batch


# ---------------------------------------------------------------------------
# Data collator
# ---------------------------------------------------------------------------

@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: object
    decoder_start_token_id: int

    def __call__(self, features):
        import torch
        from transformers import BatchFeature

        input_features = [{"input_features": f["input_features"]} for f in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        label_features = [{"input_ids": f["labels"]} for f in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(
            labels_batch.attention_mask.ne(1), -100
        )
        if (labels[:, 0] == self.decoder_start_token_id).all():
            labels = labels[:, 1:]

        batch["labels"] = labels
        return batch


# ---------------------------------------------------------------------------
# WER metric
# ---------------------------------------------------------------------------

def compute_metrics(pred, processor, metric):
    pred_ids = pred.predictions
    label_ids = pred.label_ids
    label_ids[label_ids == -100] = processor.tokenizer.pad_token_id
    pred_str = processor.tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
    label_str = processor.tokenizer.batch_decode(label_ids, skip_special_tokens=True)
    wer = 100 * metric.compute(predictions=pred_str, references=label_str)
    return {"wer": wer}


# ---------------------------------------------------------------------------
# Main training
# ---------------------------------------------------------------------------

def train(args):
    _check_deps()
    from transformers import (
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
        WhisperForConditionalGeneration,
        WhisperProcessor,
    )
    import evaluate

    print(f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    print(f"\nLoading Whisper-{args.model_size} processor...")
    model_id = f"openai/whisper-{args.model_size}"

    # Use AI4Bharat IndicWhisper if available
    if args.indic_base:
        model_id = args.indic_base
        print(f"Using Indic base: {model_id}")

    processor = WhisperProcessor.from_pretrained(model_id, language=args.language, task="transcribe")
    model = WhisperForConditionalGeneration.from_pretrained(model_id)

    model.generation_config.language = args.language
    model.generation_config.task = "transcribe"
    model.generation_config.forced_decoder_ids = None

    # Freeze encoder if --freeze_encoder (faster, use when data < 5hr)
    if args.freeze_encoder:
        for param in model.model.encoder.parameters():
            param.requires_grad = False
        print("Encoder frozen — only decoder fine-tuned.")

    print("\nLoading dataset...")
    train_ds = load_dataset_from_dir(args.data_dir, "train", processor)
    eval_ds  = load_dataset_from_dir(args.data_dir, "eval", processor)

    prepare_fn = lambda batch: prepare_dataset(batch, processor, args.language)
    train_ds = train_ds.map(prepare_fn, remove_columns=train_ds.column_names, num_proc=4)
    eval_ds  = eval_ds.map(prepare_fn, remove_columns=eval_ds.column_names, num_proc=4)

    print(f"Train: {len(train_ds)} samples | Eval: {len(eval_ds)} samples")

    collator = DataCollatorSpeechSeq2SeqWithPadding(
        processor=processor,
        decoder_start_token_id=model.config.decoder_start_token_id,
    )
    metric = evaluate.load("wer")

    training_args = Seq2SeqTrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=8,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=100,
        num_train_epochs=args.num_train_epochs,
        gradient_checkpointing=True,
        fp16=torch.cuda.is_available(),
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="wer",
        greater_is_better=False,
        predict_with_generate=True,
        generation_max_length=225,
        logging_steps=25,
        report_to="none",
        push_to_hub=False,
        dataloader_num_workers=4,
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=collator,
        compute_metrics=lambda pred: compute_metrics(pred, processor, metric),
        tokenizer=processor.feature_extractor,
    )

    print("\nStarting training...")
    trainer.train()

    print(f"\nSaving model to {args.output_dir}")
    trainer.save_model()
    processor.save_pretrained(args.output_dir)
    print("Done. Now run with --convert_only to get faster-whisper format.")


# ---------------------------------------------------------------------------
# CTranslate2 conversion (run after training)
# ---------------------------------------------------------------------------

def convert_to_ct2(hf_model_dir: str, output_dir: str, quantization: str = "int8"):
    """Convert HuggingFace Whisper → CTranslate2 (faster-whisper format)."""
    try:
        import ctranslate2
    except ImportError:
        raise SystemExit("pip install ctranslate2")

    print(f"Converting {hf_model_dir} → {output_dir} (quantization={quantization})")
    converter = ctranslate2.converters.OpusMTConverter(hf_model_dir)

    # Use Whisper converter
    import subprocess
    cmd = [
        "ct2-transformers-converter",
        "--model", hf_model_dir,
        "--output_dir", output_dir,
        "--quantization", quantization,
        "--force",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise RuntimeError("ct2-transformers-converter failed")
    print(f"Converted model at: {output_dir}")
    print("\nCopy this directory to your Mac, then set in .env:")
    print(f"  WHISPER_MODEL={output_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fine-tune Whisper for Sahayak")
    sub = parser.add_subparsers(dest="cmd")

    # train subcommand
    tr = sub.add_parser("train", help="Fine-tune Whisper on speech corpus")
    tr.add_argument("--data_dir", required=True, help="Path to dataset (train/ + eval/ subdirs)")
    tr.add_argument("--output_dir", default="./whisper-sahayak", help="Where to save HF model")
    tr.add_argument("--model_size", default="small", choices=["tiny", "base", "small", "medium"])
    tr.add_argument("--indic_base", default=None,
                    help="AI4Bharat IndicWhisper model ID, e.g. ai4bharat/indicwhisper-small-hi")
    tr.add_argument("--language", default="hi", help="Target language code")
    tr.add_argument("--num_train_epochs", type=int, default=5)
    tr.add_argument("--batch_size", type=int, default=16)
    tr.add_argument("--grad_accum", type=int, default=2,
                    help="Gradient accumulation steps (effective batch = batch_size * grad_accum)")
    tr.add_argument("--lr", type=float, default=1e-5)
    tr.add_argument("--freeze_encoder", action="store_true",
                    help="Freeze encoder weights (faster training, good for <5hr data)")

    # convert subcommand
    cv = sub.add_parser("convert", help="Convert HF model to faster-whisper (CTranslate2) format")
    cv.add_argument("--hf_model_dir", required=True)
    cv.add_argument("--output_dir", default="./whisper-sahayak-ct2")
    cv.add_argument("--quantization", default="int8", choices=["int8", "int8_float16", "float16", "float32"])

    args = parser.parse_args()

    if args.cmd == "train":
        train(args)
    elif args.cmd == "convert":
        convert_to_ct2(args.hf_model_dir, args.output_dir, args.quantization)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
