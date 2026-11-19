"""First-message eval: does the first-message step (explainer/modes/intake.py, FIRST_TALK) build when it should and answer
when it should? One Haiku Low call per row, no page built (about a cent per 20 rows).

From server/, with the venv (needs ANTHROPIC_API_KEY in ../.env):
    .venv/Scripts/python -X utf8 -m evals.intake_eval --dry-run   # list the rows, no calls
    .venv/Scripts/python -X utf8 -m evals.intake_eval             # billed
Runs: ../.variety/intake-eval/<time>.json.

Scored: the tool picked ("build" = new_topic, "answer" = answer). A plain topic answered is the costly mistake (the reader
asked for a page and didn't get one), so it is reported on its own. "offer" rows also check an answer came with an offer.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from explainer.modes import intake
from explainer.pricing import UsageLog

RUNS = Path(__file__).resolve().parents[2] / ".variety" / "intake-eval"

KL_ANSWER = ('"what topics would a KL divergence explainer include?": answered in the chat, nothing changed: "A page on KL '
             'divergence would cover: 1. what it measures, 2. the formula, 3. why it is asymmetric, 4. KL vs cross-entropy, '
             '5. where it shows up (VAEs, RLHF)." Offered an explainer: "KL divergence: what it measures, formula, asymmetry, '
             'link to cross-entropy, uses in VAEs and RLHF"')
ABSTRACT_AI = ("We introduce a parameter-efficient fine-tuning method that freezes the pretrained weights and injects trainable "
               "low-rank matrices into each Transformer layer, cutting trainable parameters by 10,000x.")
ABSTRACT_BIO = ("We report the crystal structure of a photosystem II complex from cyanobacteria at 1.9 A resolution and describe "
                "the manganese cluster that splits water during photosynthesis.")

# (label, message, expect, extra): expect "build" / "answer" / "offer" (an answer with an offer); extra: history or a paper.
CASES: list[tuple[str, str, str, dict]] = [
    ("plain topic", "LoRA", "build", {}),
    ("plain topic", "attention", "build", {}),
    ("plain topic", "beam search", "build", {}),
    ("plain topic", "variational autoencoders", "build", {}),
    ("plain topic", "Q-learning", "build", {}),
    ("plain topic", "mixture of experts", "build", {}),
    ("plain topic", "contrastive learning", "build", {}),
    ("explain X", "Explain how diffusion models generate images", "build", {}),
    ("explain X", "how does backpropagation work?", "build", {}),
    ("explain X", "teach me RLHF for beginners, light on maths", "build", {}),
    ("explain X", "I want to understand tokenization in LLMs", "build", {}),
    ("explain X", "what is dropout?", "build", {}),
    ("explain X", "why do transformers need positional encodings?", "build", {}),
    ("preview", "what topics would you include in a KL divergence explainer?", "offer", {}),
    ("preview", "what would an explainer on graph neural networks cover?", "offer", {}),
    ("short question", "in one line, is batch norm the same as layer norm?", "answer", {}),
    ("short question", "quick yes or no: does GPT use an encoder?", "answer", {}),
    ("advice", "should I learn CNNs or transformers first?", "answer", {}),
    ("chit-chat", "hi", "answer", {}),
    ("chit-chat", "thanks!", "answer", {}),
    ("app question", "what can you do?", "answer", {}),
    ("app question", "how many explainers can I make a day?", "answer", {}),
    ("not a page", "write PyTorch code for a training loop", "answer", {}),
    ("not a page", "summarize the history of AI in 3 lines", "answer", {}),
    ("non-AI", "explain photosynthesis", "answer", {}),
    ("non-AI", "how do I bake sourdough bread?", "answer", {}),
    ("non-AI", "explain the French revolution", "answer", {}),
    ("vague", "explain", "answer", {}),
    ("vague", "help", "answer", {}),
    ("after answer", "ok make it", "build", {"history": [KL_ANSWER]}),
    ("after answer", "great, build it but skip the formula", "build", {"history": [KL_ANSWER]}),
    ("after answer", "and why is it asymmetric, in short?", "answer", {"history": [KL_ANSWER]}),
    ("paper", "", "build", {"paper": ("LoRA: Low-Rank Adaptation of Large Language Models", ABSTRACT_AI)}),
    ("paper", "focus on the experiments", "build", {"paper": ("LoRA: Low-Rank Adaptation of Large Language Models", ABSTRACT_AI)}),
    ("paper question", "how much does it cut trainable parameters by?", "offer",
     {"paper": ("LoRA: Low-Rank Adaptation of Large Language Models", ABSTRACT_AI)}),
    ("non-AI paper", "", "answer", {"paper": ("Structure of photosystem II at 1.9 A", ABSTRACT_BIO)}),
]


def request_of(message: str, extra: dict) -> dict:
    req: dict = {"quality": "playground"}
    if extra.get("paper"):
        title, abstract = extra["paper"]
        req.update(mode="paper", title=title, note=message, sections=[{"heading": "Abstract", "text": abstract}])
    else:
        req.update(mode="concept", text=message)
    if extra.get("history"):
        req["history"] = extra["history"]
    return req


def run_one(client, case: tuple) -> dict:
    label, message, expect, extra = case
    usage = UsageLog("intake-eval")
    msg = intake._call(client, usage, intake.message_of(request_of(message, extra), False))
    call = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    got = {"answer": "answer", "new_topic": "build"}.get(call.name if call else "", "none")
    inp = call.input if call is not None and isinstance(call.input, dict) else {}
    want = "answer" if expect == "offer" else expect
    ok = got == want and (expect != "offer" or bool(inp.get("offer")))
    return {"label": label, "message": message, "expect": expect, "got": got, "ok": ok, "input": inp,
            "usd": usage.summary()["totalUsd"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        for c in CASES:
            print(f"{c[2]:6}  {c[0]:15}  {c[1] or '(no message)'}")
        print(f"{len(CASES)} rows")
        return
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(lambda c: run_one(client, c), CASES))
    for r in out:
        print(f"{'ok ' if r['ok'] else 'BAD'}  want {r['expect']:6} got {r['got']:6}  {r['label']:15}  {r['message'] or '(no message)'}")
        if not r["ok"]:
            print(f"      {json.dumps(r['input'], ensure_ascii=False)[:300]}")
    lost = [r for r in out if r["expect"] == "build" and r["got"] == "answer"]
    print(f"\n{sum(r['ok'] for r in out)}/{len(out)} right; builds wrongly answered (the costly mistake): {len(lost)}; "
          f"${sum(r['usd'] for r in out):.4f}")
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
