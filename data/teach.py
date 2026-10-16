"""Teacher: turn each tldr example into 2–3 casual requests a person would actually type.

  python data/teach.py --in data/out/tldr.jsonl --out data/out/tldr_requests.jsonl            # Kaggle GPU
  python data/teach.py --in data/out/tldr.jsonl --out data/out/smoke.jsonl --runner rule      # no GPU, smoke test

A request is kept only if it mentions every value in the command (file names, folders, numbers, search text),
so the model learns to use what you said, never to invent names.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

TEACHER = "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit"

SYSTEM = """You write what people type to a command-line assistant.
Given a shell command and what it does, write 3 different short requests that this exact command answers.
- Casual and short, like a quick note to a colleague. Lower case is fine.
- Mention every file name, folder name, number and search text from the command, spelled exactly.
- Do not mention flags, options or the command's name. Describe the goal, not the syntax.
- Make the 3 requests genuinely different in wording.
Reply with JSON only: {"requests": ["...", "...", "..."]}"""

FEWSHOT = [
    ({"os": "macOS", "description": "Find files matching a given size range, limiting the recursive depth to 1",
      "command": "find photos -maxdepth 1 -size +500k -size -10M"},
     {"requests": ["files in photos between 500k and 10M, top level only",
                   "which files directly inside photos are over 500k but under 10M",
                   "list photos files sized +500k to 10M without going into subfolders"]}),
    ({"os": "Linux", "description": "Create a gzipped archive and write it to a file",
      "command": "tar czf backup.tar.gz notes.txt sales.csv"},
     {"requests": ["pack notes.txt and sales.csv into backup.tar.gz",
                   "make a gzipped tarball backup.tar.gz with notes.txt and sales.csv",
                   "compress notes.txt + sales.csv into backup.tar.gz"]}),
]


def teacher_messages(row: dict) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    for ex, ans in FEWSHOT:
        msgs.append({"role": "user", "content": _user(ex)})
        msgs.append({"role": "assistant", "content": json.dumps(ans)})
    msgs.append({"role": "user", "content": _user(row)})
    return msgs


def _user(row: dict) -> str:
    return f"OS: {row['os']}\nWhat it does: {row['description']}\nCommand: {row['command']}"


# ---- validation ---------------------------------------------------------------------------------------

def _mentions(request: str, value: str) -> bool:
    r = request.lower()
    for part in value.split():
        p = part.lower().strip("'\"")
        if p.startswith("*."):                       # glob: "*.py" ↔ "py files", ".py", "*.py"
            if not re.search(rf"(\*|\b)\.?{re.escape(p[2:])}\b", r):
                return False
        elif p.strip("*") and p.strip("*") not in r:
            return False
    return True


def valid_requests(row: dict, requests: list) -> list[str]:
    out = []
    for q in requests:
        if not isinstance(q, str):
            continue
        q = " ".join(q.split()).strip(" .")
        words = len(q.split())
        if not 3 <= words <= 30 or q.lower() in (o.lower() for o in out):
            continue
        if all(_mentions(q, v) for v in row.get("values", [])):
            out.append(q)
    return out


def parse(text: str) -> list:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0)).get("requests") or []
    except (json.JSONDecodeError, AttributeError):
        return []


# ---- runners --------------------------------------------------------------------------------------------

def run_rule(rows):
    """No model: the description plus the values. Only for smoke-testing the pipeline."""
    for r in rows:
        extra = (" " + " ".join(r["values"])) if r["values"] else ""
        yield r, json.dumps({"requests": [r["description"].lower() + extra]})


def _batched_generate(model, tok, rows, batch: int, max_len: int, device: str):
    import torch

    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    items = []
    for r in rows:
        prompt = tok.apply_chat_template(teacher_messages(r), tokenize=False, add_generation_prompt=True)
        items.append((len(prompt), r, prompt))
    items.sort(key=lambda x: x[0])                      # similar lengths together: less padding
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        enc = tok([p for _, _, p in chunk], return_tensors="pt", padding=True, add_special_tokens=False).to(device)
        with torch.inference_mode():
            gen = model.generate(**enc, max_new_tokens=160, do_sample=True, temperature=0.7, top_p=0.9,
                                 use_cache=True, pad_token_id=tok.pad_token_id)
        for (_, r, _), g in zip(chunk, gen):
            yield r, tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)


def run_unsloth(rows, model: str, batch: int, max_len: int = 2048):
    from unsloth import FastLanguageModel

    m, tok = FastLanguageModel.from_pretrained(model, max_seq_length=max_len, load_in_4bit=True)
    FastLanguageModel.for_inference(m)
    yield from _batched_generate(m, tok, rows, batch, max_len, "cuda")


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runner", default="unsloth", choices=["unsloth", "rule"])
    ap.add_argument("--teacher", default=TEACHER)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.inp, encoding="utf-8")]
    if a.limit:
        rows = rows[: a.limit]
    done = set()
    out = Path(a.out)
    if out.exists():                                     # resumable: Kaggle sessions die
        done = {json.loads(l)["id"] for l in out.open(encoding="utf-8")}
    todo = [r for r in rows if r["id"] not in done]
    print(f"{len(rows)} examples, {len(todo)} to do → {out}", file=sys.stderr)
    gen = run_rule(todo) if a.runner == "rule" else run_unsloth(todo, a.teacher, a.batch)
    stats = Counter()
    with out.open("a", encoding="utf-8") as fh:
        for n, (r, text) in enumerate(gen, 1):
            reqs = valid_requests(r, parse(text))
            stats["examples"] += 1
            stats["kept_requests"] += len(reqs)
            stats["no_valid_request"] += not reqs
            fh.write(json.dumps({**r, "requests": reqs, "raw": (text or "")[:600]}, ensure_ascii=False) + "\n")
            if n % 200 == 0:
                fh.flush()
                print(f"  {n}/{len(todo)}  {dict(stats)}", file=sys.stderr)
    print(json.dumps(dict(stats)), file=sys.stderr)


if __name__ == "__main__":
    main()
