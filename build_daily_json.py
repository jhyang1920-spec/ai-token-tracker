#!/usr/bin/env python3
"""daily_raw.csv(기존 수집기 산출물) -> artifact_daily.json (표준 라이브러리만 사용)

입력 : openrouter_data/daily_raw.csv  (date,model_permaslug,total_tokens)
출력 : openrouter_data/artifact_daily.json
  dates/tot/authors : 최근 --keep일 일별 (단위 B=10억 토큰, artifact_weekly.json과 동일)
  currentWeek       : 진행 중인 주(월요일 시작, UTC) 누적, 예상 페이스(누적/경과시간×168h), 전주 합계
"""
import argparse, collections, csv, datetime as dt, json, os

CITE = "Source: OpenRouter (openrouter.ai/rankings), as of {asof}. Licensed under CC BY 4.0."


def B(x):
    return round(x / 1e9, 2)


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="openrouter_data")
    ap.add_argument("--keep", type=int, default=120)
    a = ap.parse_args()
    tot, auth = collections.Counter(), collections.defaultdict(collections.Counter)
    with open(os.path.join(a.data_dir, "daily_raw.csv"), newline="") as f:
        for r in csv.DictReader(f):
            t = int(r["total_tokens"]); d = r["date"]; s = r["model_permaslug"]
            tot[d] += t
            if s != "other":
                auth[d][s.split("/", 1)[0]] += t
    days = sorted(tot)
    if not days:
        raise SystemExit("daily_raw.csv가 비어 있음")
    last = dt.date.fromisoformat(days[-1])
    ws = monday(last)
    # 마지막 날이 일요일이면 그 주는 완료된 주 -> 진행 중 주 없음(다음 주 시작 전까지 완료 주로 표시)
    wdays = [d for d in days if ws <= dt.date.fromisoformat(d) <= last]
    el_h = ((last + dt.timedelta(days=1)) - ws).days * 24      # 마지막 수집일 끝(UTC)까지 경과
    cum = sum(tot[d] for d in wdays)
    ac = collections.Counter()
    for d in wdays:
        ac.update(auth[d])
    pw = ws - dt.timedelta(days=7)
    pdays = [d for d in days if pw <= dt.date.fromisoformat(d) < ws]
    prev = sum(tot[d] for d in pdays) if len(pdays) == 7 else None
    keep = days[-a.keep:]
    ids = sorted({i for d in keep for i in auth[d]}, key=lambda i: -sum(auth[d].get(i, 0) for d in keep[-7:]))
    asof = last.isoformat()
    try:
        asof = json.load(open(os.path.join(a.data_dir, "meta.json"))).get("as_of", asof)
    except Exception:
        pass
    out = {
        "dates": keep, "tot": [B(tot[d]) for d in keep],
        "authors": [{"id": i, "v": [B(auth[d].get(i, 0)) for d in keep]} for i in ids],
        "currentWeek": {
            "start": ws.isoformat(), "through": last.isoformat(), "days": len(wdays), "elapsedHours": el_h,
            "cum": B(cum), "pace": B(cum / el_h * 168),
            "authorsCum": {i: B(v) for i, v in ac.most_common(15)},
            "authorsPace": {i: B(v / el_h * 168) for i, v in ac.most_common(15)},
            "prevWeek": B(prev) if prev is not None else None,
            "complete": len(wdays) == 7,
        },
        "asOf": asof, "sourceUpdated": last.isoformat(), "label": "OpenRouter",
        "note": "일별 상위 50개 모델 + other 합계(주=월요일 시작, UTC). pace = 누적/경과시간×168h (추정).",
        "citation": CITE.format(asof=asof),
    }
    with open(os.path.join(a.data_dir, "artifact_daily.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    c = out["currentWeek"]
    print(f"마지막 {c['through']} · 이번 주({c['start']}) {c['days']}일 누적 {c['cum']}B · 예상 페이스 {c['pace']}B · 전주 {c['prevWeek']}B")


if __name__ == "__main__":
    main()
