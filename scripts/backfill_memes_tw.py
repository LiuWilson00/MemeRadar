#!/usr/bin/env python
"""把 memes.tw 回填到指定深度：分段呼叫爬蟲、記錄游標、可隨時中斷續跑。

為什麼要這支：回填到深度 2 萬是十幾個小時的工作，任何一次中斷（逾時被砍、關機、
網路斷）都不該讓進度歸零。這裡把它拆成一段一段，每段結束就把游標寫回檔案；重跑時
從游標接著走。就算游標檔不見了也只是重掃一遍（去重會擋掉已入庫的，慢但不會重複匯入）。

深度＝從最新往回數第幾張。爬蟲的 --skip 直接換算成 API 頁碼，所以跳過已完成的那段
不必發任何請求（見 memeradar/ingestion/memes_tw.py）。

用法（在 repo 根目錄）：
    # 一路跑到深度 2 萬；中斷後原指令再跑一次即可續跑
    python scripts/backfill_memes_tw.py --target-depth 20000

    # 想先小試
    python scripts/backfill_memes_tw.py --target-depth 4000 --chunk 200

成本：標註走 config.vlm_model（目前 qwen3.5-flash），實測約 $0.21 / 1000 張。
--max-images 是防呆上限，避免參數打錯就無限燒下去。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURSOR = ROOT / "data" / "backfill_cursor.json"
#: 同一段連續失敗幾次就跳過。memes.tw 有固定回 500 的頁（如 page 504），
#: 無上限重試會讓整趟回填卡在原地空轉。
MAX_SEGMENT_RETRIES = 3


def _load_cursor(start: int) -> int:
    if CURSOR.exists():
        try:
            return int(json.loads(CURSOR.read_text(encoding="utf-8"))["depth"])
        except (ValueError, KeyError, OSError):
            pass
    return start


def _save_cursor(depth: int) -> None:
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({"depth": depth}), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="memes.tw 分段回填（可中斷續跑）")
    p.add_argument("--target-depth", type=int, default=20000, help="回填到第幾張（從最新算起）")
    p.add_argument("--start-depth", type=int, default=3000, help="游標檔不存在時從哪裡開始")
    p.add_argument("--chunk", type=int, default=400, help="每段掃幾張")
    p.add_argument("--workers", type=int, default=8, help="並行標註緒數")
    p.add_argument("--max-images", type=int, default=30000, help="防呆：本次最多處理幾張")
    p.add_argument("--reset", action="store_true", help="清掉游標，從 --start-depth 重來")
    args = p.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if args.reset and CURSOR.exists():
        CURSOR.unlink()

    depth = _load_cursor(args.start_depth)
    if depth >= args.target_depth:
        print(f"✅ 已達目標深度（游標 {depth} ≥ {args.target_depth}），無事可做。")
        return 0

    print(f"回填 memes.tw：深度 {depth} → {args.target_depth}，每段 {args.chunk} 張、"
          f"{args.workers} 緒。中斷後重跑同一指令即可續跑。\n")
    processed = 0
    fails = 0
    skipped: list[int] = []
    t_all = time.perf_counter()
    while depth < args.target_depth and processed < args.max_images:
        chunk = min(args.chunk, args.target_depth - depth)
        cmd = [
            sys.executable, str(ROOT / "scripts" / "crawl_memes_tw.py"),
            "--count", str(chunk), "--skip", str(depth), "--ignore-watermark",
            "--local-annotate", "--workers", str(args.workers),
        ]
        t0 = time.perf_counter()
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        tail = [ln for ln in (result.stdout or "").splitlines() if "完成：" in ln]
        el = time.perf_counter() - t0
        if result.returncode != 0:
            # 單段失敗（多半是 memes.tw 逾時）不該讓整趟停：退避後重試同一段。
            # 但**不能無限重試**——memes.tw 有些頁是固定回 500 的（2026-08-02：page 504
            # 每次都 500），那種段落重試幾次都一樣，會把整趟回填卡死在原地空轉。
            # 重試上限到了就跳過該段、把游標推進，並大聲記下來。
            fails += 1
            tail_err = (result.stderr or "").strip().splitlines()[-1:] or ["(無 stderr)"]
            if fails < MAX_SEGMENT_RETRIES:
                print(f"  ⚠ 深度 {depth} 失敗（{el:.0f}s，第 {fails} 次），30 秒後重試："
                      f"{tail_err[0][:120]}", flush=True)
                time.sleep(30)
                continue
            print(f"  ⏭ 深度 {depth}→{depth + chunk} 連續失敗 {fails} 次，跳過這一段："
                  f"{tail_err[0][:120]}", flush=True)
            skipped.append(depth)
            depth += chunk
            processed += chunk
            fails = 0
            _save_cursor(depth)
            continue
        fails = 0
        print(f"  深度 {depth:>6} → {depth + chunk:<6} {el:>5.0f}s  "
              f"{tail[0].strip() if tail else '(無摘要)'}", flush=True)
        depth += chunk
        processed += chunk
        _save_cursor(depth)

    mins = (time.perf_counter() - t_all) / 60
    done = depth >= args.target_depth
    print(f"\n{'✅ 已達目標深度' if done else '⏸ 本次到此'} {depth}／{args.target_depth}，"
          f"本次處理 {processed} 張、耗時 {mins:.0f} 分。")
    if skipped:
        # 靜靜跳過等於謊報覆蓋率：跑完顯示「已達目標深度」，實際上中間有洞。
        # 一定要把起始深度印出來，日後才知道要補哪幾段。
        print(f"   ⚠ 有 {len(skipped)} 段連續失敗被跳過，"
              f"共約 {len(skipped) * args.chunk} 張沒抓到。")
        print(f"     起始深度：{skipped}")
        print(f"     單獨補跑：python scripts/backfill_memes_tw.py --start-depth <深度> "
              f"--target-depth <深度+{args.chunk}> --reset")
    if not done:
        print("   續跑：重新執行同一條指令即可（游標已存 data/backfill_cursor.json）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
