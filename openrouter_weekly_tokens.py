#!/usr/bin/env python3
"""
OpenRouter 주간 토큰 사용량 수집기

OpenRouter Data API의 /api/v1/datasets/rankings-daily(일간 상위 50개 모델 + other)를
받아서 주간(월요일 시작, UTC) 합계 CSV를 만든다.

출력 (--out-dir, 기본 ./openrouter_data):
  daily_raw.csv          일간 원본 (date, model_permaslug, total_tokens) — 누적 저장소
  weekly_by_model.csv    주간 모델별 토큰 합계·점유율·순위
  weekly_by_author.csv   주간 저자(메이커)별 토큰 합계·점유율·전주 대비 증감
  meta.json              마지막 수집 시각(as_of)과 인용 문구

사용법:
  export OPENROUTER_API_KEY=sk-or-...
  python openrouter_weekly_tokens.py                    # 증분 업데이트(처음이면 최근 52주)
  python openrouter_weekly_tokens.py --start 2025-01-01 # 데이터셋 시작일부터 전체 백필
  python openrouter_weekly_tokens.py --include-partial  # 진행 중인 이번 주도 포함

외부 라이브러리 없이 파이썬 3.9+ 표준 라이브러리만 사용한다.

주의:
  - API는 하루 상위 50개 모델만 개별 행으로 주고 나머지는 'other'로 합친다.
    어떤 모델이 특정 날 50위 밖이면 그날 토큰은 'other'에 들어가므로,
    순위가 낮은 모델의 주간 합계는 과소 집계될 수 있다. 전체 합계(other 포함)는 정확하다.
  - 토큰 수는 각 제공사 토크나이저 기준이라 제공사 간 1:1 비교는 근사치다.
  - 재게시 시 인용 필수(CC BY 4.0): "Source: OpenRouter (openrouter.ai/rankings), as of <as_of>."
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
DATASET_START = date(2025, 1, 1)
MAX_SPAN_DAYS = 366          # API 최대 조회 구간
RESTATE_DAYS = 7             # 최근 데이터는 재집계될 수 있어 매번 다시 받는 일수
DEFAULT_LOOKBACK_WEEKS = 52  # 첫 실행 시 기본 백필 기간

DATE_SUFFIX = re.compile(r"-\d{8}$")


# ---------------------------------------------------------------- API 호출

def fetch_range(api_key: str, start: date, end: date, max_retries: int = 5) -> dict:
    """한 구간(최대 366일)을 받아온다. 429/5xx는 지수 백오프로 재시도."""
    qs = urllib.parse.urlencode({"start_date": start.isoformat(), "end_date": end.isoformat()})
    req = urllib.request.Request(
        f"{API_URL}?{qs}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                wait = 2 ** attempt * 5
                print(f"  HTTP {e.code}, {wait}초 후 재시도...", file=sys.stderr)
                time.sleep(wait)
                continue
            body = e.read().decode("utf-8", "replace")[:300]
            raise SystemExit(f"API 오류 {e.code}: {body}")
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 5)
                continue
            raise SystemExit(f"네트워크 오류: {e.reason}")
    raise SystemExit("재시도 횟수 초과")


def fetch_all(api_key: str, start: date, end: date) -> tuple[list[dict], dict]:
    """366일 제한에 맞춰 구간을 나눠 받는다."""
    rows: list[dict] = []
    meta: dict = {}
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=MAX_SPAN_DAYS - 1), end)
        print(f"  수집: {cur} ~ {chunk_end}")
        payload = fetch_range(api_key, cur, chunk_end)
        rows.extend(payload.get("data", []))
        meta = payload.get("meta", meta)
        cur = chunk_end + timedelta(days=1)
        if cur <= end:
            time.sleep(2.5)  # 분당 30회 제한 여유
    return rows, meta


# ---------------------------------------------------------------- 원본 저장소

def load_daily(path: Path) -> dict[tuple[str, str], int]:
    store: dict[tuple[str, str], int] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                store[(r["date"], r["model_permaslug"])] = int(r["total_tokens"])
    return store


def save_daily(path: Path, store: dict[tuple[str, str], int]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "model_permaslug", "total_tokens"])
        for (d, m), t in sorted(store.items(), key=lambda kv: (kv[0][0], -kv[1])):
            w.writerow([d, m, t])


def merge_rows(store: dict[tuple[str, str], int], rows: list[dict]) -> None:
    """새로 받은 날짜는 통째로 교체(재집계·순위 변동 반영)."""
    fetched_dates = {r["date"] for r in rows}
    for key in [k for k in store if k[0] in fetched_dates]:
        del store[key]
    for r in rows:
        store[(r["date"], r["model_permaslug"])] = int(r["total_tokens"])


# ---------------------------------------------------------------- 집계

def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # 월요일


def author_of(slug: str) -> str:
    return "other" if slug == "other" else slug.split("/", 1)[0]


def model_group_of(slug: str) -> str:
    """'deepseek/deepseek-v4.1-flash-20260910:free' -> 'deepseek/deepseek-v4.1-flash:free'"""
    if slug == "other":
        return slug
    base, sep, variant = slug.partition(":")
    return DATE_SUFFIX.sub("", base) + (sep + variant if sep else "")


def aggregate(store: dict[tuple[str, str], int], last_complete_day: date, include_partial: bool):
    by_model: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    days_seen: dict[date, set] = defaultdict(set)

    for (d_str, slug), tokens in store.items():
        d = date.fromisoformat(d_str)
        if d > last_complete_day:
            continue
        ws = week_start(d)
        by_model[ws][slug] += tokens
        days_seen[ws].add(d)

    weeks = sorted(by_model)
    if not include_partial:
        weeks = [w for w in weeks if len(days_seen[w]) == 7]

    model_rows, author_rows = [], []
    prev_author: dict[str, int] = {}

    for ws in weeks:
        models = by_model[ws]
        total = sum(models.values())
        n_days = len(days_seen[ws])
        complete = n_days == 7

        ranked = sorted(((m, t) for m, t in models.items() if m != "other"), key=lambda x: -x[1])
        for rank, (slug, t) in enumerate(ranked, 1):
            model_rows.append({
                "week_start": ws.isoformat(),
                "days": n_days,
                "complete_week": complete,
                "rank": rank,
                "author": author_of(slug),
                "model_permaslug": slug,
                "model_group": model_group_of(slug),
                "total_tokens": t,
                "share_pct": round(t / total * 100, 3) if total else 0,
            })
        if "other" in models:
            model_rows.append({
                "week_start": ws.isoformat(), "days": n_days, "complete_week": complete,
                "rank": "", "author": "other", "model_permaslug": "other", "model_group": "other",
                "total_tokens": models["other"],
                "share_pct": round(models["other"] / total * 100, 3) if total else 0,
            })

        authors: dict[str, int] = defaultdict(int)
        for slug, t in models.items():
            authors[author_of(slug)] += t
        for rank, (a, t) in enumerate(sorted(authors.items(), key=lambda x: -x[1]), 1):
            prev = prev_author.get(a)
            author_rows.append({
                "week_start": ws.isoformat(),
                "days": n_days,
                "complete_week": complete,
                "rank": rank,
                "author": a,
                "total_tokens": t,
                "share_pct": round(t / total * 100, 3) if total else 0,
                "wow_change_pct": round((t / prev - 1) * 100, 2) if prev else "",
                "week_total_tokens": total,
            })
        prev_author = dict(authors)

    return model_rows, author_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description="OpenRouter 주간 토큰 사용량 CSV 생성")
    p.add_argument("--out-dir", default="openrouter_data")
    p.add_argument("--start", help="수집 시작일 YYYY-MM-DD (지정 시 해당 기간 강제 재수집)")
    p.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: 어제 UTC)")
    p.add_argument("--include-partial", action="store_true", help="7일이 안 찬 주도 출력")
    args = p.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("환경변수 OPENROUTER_API_KEY를 설정하세요.")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    daily_path = out / "daily_raw.csv"
    store = load_daily(daily_path)

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    end = date.fromisoformat(args.end) if args.end else yesterday

    if args.start:
        start = date.fromisoformat(args.start)
    elif store:
        last = max(date.fromisoformat(d) for d, _ in store)
        start = last - timedelta(days=RESTATE_DAYS)
    else:
        start = week_start(end) - timedelta(weeks=DEFAULT_LOOKBACK_WEEKS)
    start = max(start, DATASET_START)

    print(f"OpenRouter rankings-daily 수집: {start} ~ {end}")
    rows, meta = fetch_all(api_key, start, end)
    print(f"  {len(rows):,}행 수신")

    merge_rows(store, rows)
    save_daily(daily_path, store)

    model_rows, author_rows = aggregate(store, end, args.include_partial)
    write_csv(out / "weekly_by_model.csv", model_rows)
    write_csv(out / "weekly_by_author.csv", author_rows)

    as_of = meta.get("as_of", "")
    (out / "meta.json").write_text(json.dumps({
        "as_of": as_of,
        "fetched_range": [start.isoformat(), end.isoformat()],
        "daily_rows": len(store),
        "citation": f"Source: OpenRouter (openrouter.ai/rankings), as of {as_of}. Licensed under CC BY 4.0.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 콘솔 요약: 최근 완료 주의 저자별 점유율
    if author_rows:
        latest = author_rows[-1]["week_start"]
        print(f"\n최근 주 ({latest} 시작) 저자별 토큰:")
        for r in [r for r in author_rows if r["week_start"] == latest][:10]:
            wow = f"{r['wow_change_pct']:+.1f}%" if r["wow_change_pct"] != "" else "  n/a"
            print(f"  {r['rank']:>2}. {r['author']:<20} {r['total_tokens'] / 1e12:>8.2f}T  "
                  f"{r['share_pct']:>6.2f}%  WoW {wow}")
    print(f"\n저장 완료 → {out.resolve()}")


if __name__ == "__main__":
    main()
