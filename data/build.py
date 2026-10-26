"""Merge teacher-written tldr requests + verified synthetic tasks into chat-format training data.

  python data/build.py --tldr data/out/tldr_requests.jsonl --synth data/out/synth.jsonl --out data/out/master

Every example uses boop's own prompt (boop.prompt.build_messages, without few-shot examples), so the model
is trained on exactly what it sees at run time. The eval tasks (eval/tasks.jsonl) are never trained on;
a check refuses to write the data if a training request looks like one of them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boop.context import Context  # noqa: E402
from boop.prompt import build_messages  # noqa: E402

sys.path.insert(0, str(ROOT / "data"))
from tldr import EVERYDAY, _ASKS_AN_AI  # noqa: E402

_CWD = {"Linux": ["/home/sam/project", "/home/ana/work/site", "/srv/app"],
        "macOS": ["/Users/sam/project", "/Users/ana/Documents/work", "/Users/lee/code/api"],
        "Windows": ["C:\\Users\\sam\\project", "C:\\Users\\ana\\Documents\\work", "D:\\code\\api"]}
_FILLER = ["README.md", "src/", "notes.txt", "package.json", "docs/", "Makefile", ".git/", "tests/", "main.py"]


def bucket(key: str) -> float:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def tldr_examples(path: str, rng: random.Random):
    """Teacher output → examples. The folder listing includes the files the command uses, as it would."""
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        # teacher output from an older allowlist: keep only what today's tldr.py would keep
        first = (r.get("tool") or "").split()[0].lower() if r.get("tool") else ""
        if r.get("platform") != "windows" and first and first not in EVERYDAY:
            continue
        if _ASKS_AN_AI.search(r.get("command", "")):
            continue
        for q in r.get("requests", []):
            files = sorted({v for val in r["values"] for v in val.split() if "." in v and "/" not in v
                            and not v.startswith(("*", "-", "http"))} | set(rng.sample(_FILLER, 4)))
            yield {"group": f"tldr:{r['platform']}/{r['tool']}", "source": "tldr", "os": r["os"],
                   "shell": r["shell"], "cwd": rng.choice(_CWD[r["os"]]), "files": files, "request": q,
                   "command": r["command"], "explanation": r["description"].rstrip(".") + ".", "piped": None}


def synth_examples(path: str, rng: random.Random):
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        yield {**r, "group": f"synth:{r['template']}", "cwd": rng.choice(_CWD[r["os"]])}


def to_chat(ex: dict) -> dict:
    ctx = Context(os=ex["os"], shell=ex["shell"], cwd=ex["cwd"], files=ex["files"], piped=ex.get("piped"),
                  known=[tuple(k) for k in ex.get("known") or []])
    msgs = build_messages(ex["request"], ctx, examples=False)
    msgs.append({"role": "assistant", "content": json.dumps({"command": ex["command"],
                                                             "explanation": ex["explanation"]})})
    return {"messages": msgs, "meta": {"source": ex["source"], "group": ex["group"], "os": ex["os"]}}


def eval_overlap(requests: list[str]) -> list[tuple[str, str]]:
    """Training requests that are near-copies of an eval request (ignoring file names)."""
    tasks = [json.loads(l)["request"].lower() for l in (ROOT / "eval/tasks.jsonl").read_text().splitlines() if l.strip()]
    strip = lambda t: {w for w in t.lower().split() if "." not in w and "/" not in w}
    evals = [(t, strip(t)) for t in tasks]
    bad = []
    for q in requests:
        words = strip(q)
        for t, tw in evals:
            if tw and len(words & tw) / len(tw | words) >= 0.8:
                bad.append((q, t))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("--tldr", action="append", default=[])
    ap.add_argument("--synth", action="append", default=[])
    ap.add_argument("--out", default="data/out/master")
    ap.add_argument("--max-tldr", type=int, default=None,
                    help="cap tldr examples; default: half the synth count (verified synth is the core skill set)")
    ap.add_argument("--valid-frac", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    tldr = [e for p in a.tldr for e in tldr_examples(p, rng)]
    rng.shuffle(tldr)
    synth = [e for p in a.synth for e in synth_examples(p, rng)]
    tldr = tldr[: len(synth) // 2 if a.max_tldr is None else a.max_tldr]
    allx = tldr + synth

    # requests that read like an eval task would let the model memorise the test: drop them, don't train on them
    bad = eval_overlap([e["request"] for e in allx])
    if bad:
        drop = {q for q, _ in bad}
        for q, t in bad[:10]:
            print(f"  dropped (too close to eval): {q!r} ~ {t!r}", file=sys.stderr)
        allx = [e for e in allx if e["request"] not in drop]
        print(f"dropped {len(drop)} eval look-alikes", file=sys.stderr)

    seen, splits, stats = set(), {"train": [], "valid": []}, {"train": Counter(), "valid": Counter()}
    for ex in allx:
        # same request in a different folder is a different lesson (read the listing), so the folder is in the key
        key = (ex["os"], ex["shell"], ex["request"].lower(), ex["command"], tuple(ex["files"]))
        if key in seen:
            continue
        seen.add(key)
        split = "valid" if bucket(ex["group"]) < a.valid_frac else "train"   # whole tools/templates held out
        splits[split].append(to_chat(ex))
        stats[split].update([f"source:{ex['source']}", f"os:{ex['os']}", "examples"])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        rng.shuffle(rows)
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {k: dict(sorted(v.items())) for k, v in stats.items()}
    (out / "stats.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
