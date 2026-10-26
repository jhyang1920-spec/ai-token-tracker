#!/usr/bin/env python3
"""
weekly_by_author.csv / weekly_by_model.csv 를 아티팩트(기업 AI 채택률 트래커)가 읽는
JSON 두 개로 변환한다. openrouter_weekly_tokens.py 다음에 실행.

출력: <out-dir>/artifact_weekly.json, <out-dir>/artifact_models.json
  - 토큰 단위는 10억(B), 소수 둘째 자리
"""
import argparse, csv, json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LAB = {"anthropic":"Anthropic","openai":"OpenAI","google":"Google","x-ai":"xAI","deepseek":"DeepSeek",
 "qwen":"Qwen","alibaba":"Alibaba","meta-llama":"Meta Llama","meta":"Meta","mistralai":"Mistral",
 "minimax":"MiniMax","moonshotai":"Moonshot","z-ai":"Z.ai","nvidia":"NVIDIA","xiaomi":"Xiaomi",
 "stepfun":"StepFun","typesafe":"Typesafe","other":"기타(상위 50위 밖)","tngtech":"TNG",
 "openrouter":"OpenRouter","microsoft":"Microsoft","tencent":"Tencent","cohere":"Cohere",
 "poolside":"Poolside","nousresearch":"Nous","upstage":"Upstage","amazon":"Amazon","gryphe":"Gryphe",
 "inclusionai":"inclusionAI","liquid":"Liquid","arcee-ai":"Arcee","thedrummer":"TheDrummer",
 "nex-agi":"Nex AGI","dots-studio":"dots","thinkingmachines":"Thinking Machines",
 "perplexity":"Perplexity","ai21":"AI21","bytedance-seed":"ByteDance Seed","baidu":"Baidu",
 "stealth":"stealth"}
B = lambda v: round(int(v) / 1e9, 2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="openrouter_data")
    ap.add_argument("--out-dir", default="openrouter_data")
    a = ap.parse_args()
    d, o = Path(a.data_dir), Path(a.out_dir)

    A = list(csv.DictReader(open(d / "weekly_by_author.csv", encoding="utf-8-sig")))
    M = list(csv.DictReader(open(d / "weekly_by_model.csv", encoding="utf-8-sig")))
    weeks = sorted({r["week_start"] for r in A if r["complete_week"] == "True"})
    wi = {w: i for i, w in enumerate(weeks)}; n = len(weeks)

    tot = [0] * n; auth = defaultdict(lambda: [0] * n)
    for r in A:
        i = wi.get(r["week_start"])
        if i is None: continue
        auth[r["author"]][i] = int(r["total_tokens"]); tot[i] = int(r["week_total_tokens"])

    as_of = datetime.now(timezone.utc).date().isoformat()
    meta_p = d / "meta.json"
    if meta_p.exists():
        as_of = (json.loads(meta_p.read_text(encoding="utf-8")).get("as_of") or as_of)[:10]

    weekly = {"weeks": weeks, "tot": [B(x) for x in tot],
              "authors": [{"id": k, "label": LAB.get(k, k), "v": [B(x) for x in v]}
                          for k, v in sorted(auth.items(), key=lambda kv: -sum(kv[1]))],
              "asOf": as_of, "label": "OpenRouter", "sourceUpdated": as_of,
              "citation": "Source: OpenRouter (openrouter.ai/rankings). Licensed under CC BY 4.0."}

    mods = defaultdict(lambda: {"a": "", "p": {}})
    for r in M:
        i = wi.get(r["week_start"])
        if i is None: continue
        g = mods[r["model_group"]]; g["a"] = r["author"]
        g["p"][i] = g["p"].get(i, 0) + int(r["total_tokens"])
    models = {"weeks": weeks, "sourceUpdated": as_of,
              "models": [{"m": k, "a": g["a"], "p": [[i, B(v)] for i, v in sorted(g["p"].items())]}
                         for k, g in mods.items()]}

    o.mkdir(parents=True, exist_ok=True)
    for name, obj in (("artifact_weekly.json", weekly), ("artifact_models.json", models)):
        (o / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"artifact JSON 생성: {n}주 ({weeks[0]} ~ {weeks[-1]}), as_of {as_of}")

if __name__ == "__main__":
    main()
