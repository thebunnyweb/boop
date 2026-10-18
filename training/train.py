"""Fine-tune a small model (default SmolLM2-360M-Instruct) on boop's data with Unsloth, export GGUF.

  python training/train.py --data data/out/master --out runs/boop --gguf q8_0,q4_k_m \
      --push bunnyweb/boop-360m-gguf                 # push needs HF_TOKEN

Small models train fast, so this uses 16-bit LoRA with a high rank (no 4-bit loading), or a full fine-tune
with --full. Loss is computed on the assistant's JSON only.
"""
from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path

BASE = "HuggingFaceTB/SmolLM2-360M-Instruct"


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("--data", default="data/out/master")
    ap.add_argument("--data-hf", default="bunnyweb/boop-data", help="HF dataset to fetch master/ from if --data is missing")
    ap.add_argument("--out", default="runs/boop")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--max-seq", type=int, default=1024)
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=None, help="default 2e-4 (LoRA) / 5e-5 (--full)")
    ap.add_argument("--rank", type=int, default=64)
    ap.add_argument("--full", action="store_true", help="full fine-tune instead of LoRA")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--max-steps", type=int, default=-1, help="e.g. 30 for a smoke test")
    ap.add_argument("--gguf", default="q8_0,q4_k_m", help="comma-separated quantizations to export ('' = none)")
    ap.add_argument("--push", default=None, help="HF repo id for the GGUF files (uses HF_TOKEN)")
    a = ap.parse_args()
    a.data = find_data(a.data, a.data_hf)

    from unsloth import FastLanguageModel, is_bfloat16_supported   # before transformers/trl: applies patches
    from unsloth.chat_templates import train_on_responses_only
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    kw = dict(max_seq_length=a.max_seq, load_in_4bit=False)
    if a.full:
        kw["full_finetuning"] = True
    model, tokenizer = FastLanguageModel.from_pretrained(a.base, **kw)
    if not a.full:
        model = FastLanguageModel.get_peft_model(
            model, r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0, bias="none",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            use_gradient_checkpointing="unsloth", random_state=13)

    ds = load_dataset("json", data_files={"train": f"{a.data}/train.jsonl", "valid": f"{a.data}/valid.jsonl"})
    ds = ds.map(lambda b: {"text": [tokenizer.apply_chat_template(m, tokenize=False) for m in b["messages"]]},
                batched=True, remove_columns=ds["train"].column_names)

    cfg = dict(
        dataset_text_field="text", packing=False,
        per_device_train_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
        num_train_epochs=a.epochs, max_steps=a.max_steps, learning_rate=a.lr or (5e-5 if a.full else 2e-4),
        lr_scheduler_type="cosine", warmup_ratio=0.03, weight_decay=0.01,
        logging_steps=20, eval_steps=200, save_steps=400, save_total_limit=2,
        fp16=not is_bfloat16_supported(), bf16=is_bfloat16_supported(),        # T4: fp16
        optim="adamw_8bit", seed=13, output_dir=f"{a.out}/checkpoints", report_to="none",
    )
    fields = set(inspect.signature(SFTConfig.__init__).parameters)              # trl renamed fields over time
    cfg["max_length" if "max_length" in fields else "max_seq_length"] = a.max_seq
    cfg["eval_strategy" if "eval_strategy" in fields else "evaluation_strategy"] = "steps"

    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=ds["train"],
                         eval_dataset=ds["valid"], args=SFTConfig(**cfg))
    # SmolLM2 and Qwen both use ChatML; learn only the answer, not the prompt
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    trainer.train()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out / "adapter")
    tokenizer.save_pretrained(out / "adapter")
    print(f"saved → {out / 'adapter'}")

    quants = [q for q in a.gguf.split(",") if q]
    if quants:
        model.save_pretrained_gguf(str(out / "gguf"), tokenizer, quantization_method=quants)
        files = [pick_gguf(out, q) for q in quants]
        print("GGUF:", *[f"{f} ({f.stat().st_size / 1e6:.0f} MB)" for f in files], sep="\n  ")
        if a.push:
            from push_to_hub import push
            push(a.push, files, base=a.base, token=os.environ.get("HF_TOKEN"))


def find_data(path: str, hf_repo: str) -> str:
    """The local build if there is one, else master/ from the HF dataset. Fails before loading any model."""
    if (Path(path) / "train.jsonl").exists():
        return path
    if not hf_repo:
        raise SystemExit(f"no {path}/train.jsonl")
    from huggingface_hub import HfApi, snapshot_download
    token = os.environ.get("HF_TOKEN")
    files = HfApi(token=token).list_repo_files(hf_repo, repo_type="dataset")
    if "master/train.jsonl" not in files:
        raise SystemExit(f"no {path}/train.jsonl, and no master/train.jsonl in {hf_repo}; it has: {files}")
    d = Path(snapshot_download(hf_repo, repo_type="dataset", allow_patterns=["master/*"], token=token)) / "master"
    print(f"no {path}/train.jsonl: using {hf_repo} → {d}\n{(d / 'stats.json').read_text()}")
    return str(d)


def pick_gguf(out: Path, quant: str) -> Path:
    """Unsloth also leaves an F16 .gguf around; pick the quantization we asked for."""
    hits = [f for f in sorted(out.rglob("*.gguf")) if quant.upper() in f.name.upper()]
    if not hits:
        raise SystemExit(f"no {quant} .gguf under {out}: {[f.name for f in out.rglob('*.gguf')]}")
    return hits[0]


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
