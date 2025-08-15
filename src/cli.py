from datetime import date, datetime, timedelta
from pathlib import Path
from textwrap import shorten
import os
import sys

# pandas 與 rich（已在專案中）
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box

from .enrich import enrich_word, predict_pos
from .gsheets import (
    add_word, read_df, due_reviews, schedule_next,
    bulk_import_csv, backup_to_csv, open_ws
)

# =========================
# 小工具
# =========================
def ask(prompt: str, default: str | None = None) -> str:
    tip = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{tip}: ").strip()
    return val if val else (default or "")

def ask_date(prompt: str, default: str | None = None) -> str:
    """
    使用者輸入日期工具：
    - 可輸入 today / t / now / tomorrow / tmr
    - 支援 YYYY-MM-DD 與 YYYY/MM/DD（會自動轉成 -）
    - 解析成功後回傳 ISO 日期字串
    """
    while True:
        v = ask(prompt, default)
        if not v:
            return ""
        try:
            low = v.lower().strip()
            if low in ("today", "t", "now"):
                return date.today().isoformat()
            if low in ("tomorrow", "tmr"):
                return (date.today() + timedelta(days=1)).isoformat()
            v = v.replace("/", "-")
            return datetime.fromisoformat(v).date().isoformat()
        except Exception:
            print("⚠ 日期格式請用 YYYY-MM-DD（或輸入 today / tomorrow）")

def pause():
    input("\n(按 Enter 繼續) ")

def header(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

# =========================
# 共用工具
# =========================
def _parse_date_str(s: str | None) -> date:
    """允許空白=今天、today/tomorrow、YYYY-MM-DD / YYYY/MM/DD。"""
    if not s:
        return date.today()
    s = s.strip().lower()
    if s in ("today", "t", "now", ""):
        return date.today()
    if s in ("tomorrow", "tmr"):
        return date.today() + timedelta(days=1)
    s = s.replace("/", "-")
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        raise ValueError("⚠ 日期格式請用 YYYY-MM-DD（或輸入 today / tomorrow）")

def _format_cell(x, width: int) -> str:
    """純文字模式下安全截斷；保留單行輸出。"""
    if x is None:
        return ""
    s = str(x).replace("\n", " ").replace("\r", " ").strip()
    return shorten(s, width=width, placeholder="…")

def _paginate(rows, page_size: int = 20):
    """簡易分頁：印一頁停一下；Enter 繼續，q 離開。"""
    total = len(rows)
    if total <= page_size:
        yield rows
        return
    for i in range(0, total, page_size):
        chunk = rows[i:i + page_size]
        yield chunk
        if i + page_size < total:
            ans = input(f"\n-- 已顯示 {i + page_size}/{total} 筆，按 Enter 繼續，q 離開：").strip().lower()
            if ans == "q":
                break

# =========================
# 功能：新增單字（互動）
# =========================
def action_add_word():
    header("新增單字（互動式輸入）")
    word = ask("Word（英文單字）")
    if not word:
        print("⚠ 必填：Word")
        return

    # 自動預測詞性（使用者可覆寫）
    auto_pos = predict_pos(word) or "n."
    pos = ask("POS（詞性，如 n. / v. / adj.）", auto_pos)

    meaning = ask("Meaning（中文）")
    example = ask("Example（例句）")
    synonyms = ask("Synonyms（用 | 分隔）")
    topic = ask("Topic（主題）")
    source = ask("Source（出處）")
    review_date = ask_date("Review Date（YYYY-MM-DD / today / tomorrow）", date.today().isoformat())
    note = ask("Note（備註）")

    added = add_word({
        "Word": word, "POS": pos, "Meaning": meaning, "Example": example,
        "Synonyms": synonyms, "Topic": topic, "Source": source,
        "Review Date": review_date, "Note": note
    })
    # add_word 在 gsheets.py 內會做 Word+Meaning 去重；回傳 None/False 皆視為已處理
    if added is False:
        print(f"⏩ 已跳過重複：{word}")
    else:
        print(f"✅ 已新增：{word}")
    pause()

# =========================
# 功能：到期複習清單（優化版）
# =========================
def action_due_reviews():
    header("到期複習清單")
    # 友善日期輸入：留空=今天，也接受 today/tomorrow
    raw = ask_date("查詢日期（留空=今天；today / tomorrow 也可）", "")
    try:
        as_of = _parse_date_str(raw)
    except ValueError as e:
        print(e)
        pause()
        return

    df = due_reviews(as_of)
    if df.empty:
        print("🎉 今天沒有到期要複習的單字")
        pause()
        return

    # 逾期天數（None 視為最後）
    if "Review Date" in df.columns:
        def _safe(d):
            try:
                return _parse_date_str(str(d))
            except Exception:
                return None
        df["_review_dt"] = df["Review Date"].map(_safe)
    else:
        df["_review_dt"] = None

    df["逾期天數"] = df["_review_dt"].map(lambda d: (as_of - d).days if isinstance(d, date) else None)

    pref_cols = ["Word", "POS", "Meaning", "Example", "Synonyms", "Topic", "Review Date", "逾期天數"]
    cols = [c for c in pref_cols if c in df.columns]
    if "逾期天數" not in cols and "逾期天數" in df.columns:
        cols.append("逾期天數")

    # 依逾期程度大→小、Topic、Word 排序
    df_sorted = df.sort_values(by=["逾期天數", "Topic", "Word"], ascending=[False, True, True], na_position="last")

    total = len(df_sorted)
    print(f"📅 截止：{as_of.isoformat()}　需複習：{total} 筆\n")

    # 欄寬規劃（可依需要調整）
    width_map = {
        "Word": 18, "POS": 6, "Meaning": 36, "Example": 36,
        "Synonyms": 24, "Topic": 12, "Review Date": 12, "逾期天數": 6
    }
    width_map = {k: v for k, v in width_map.items() if k in cols}
    rows = df_sorted[cols].fillna("").to_dict("records")

    # 有 rich → 漂亮表格；沒有 → 純文字整齊化
    try:
        console = Console()
        table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)
        for c in cols:
            table.add_column(
                c,
                max_width=width_map.get(c, 20),
                overflow="fold",
                no_wrap=(c in ["Word", "POS", "Review Date", "逾期天數"])
            )
        page_size = int(os.getenv("DUE_PAGE_SIZE", "20"))
        shown = 0
        for chunk in _paginate(rows, page_size=page_size):
            for row in chunk:
                table.add_row(*[str(row.get(c, "")) for c in cols])
            console.print(table)
            shown += len(chunk)
            if shown < total:
                # 重新建一張 table，避免前頁重複
                table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)
                for c in cols:
                    table.add_column(
                        c,
                        max_width=width_map.get(c, 20),
                        overflow="fold",
                        no_wrap=(c in ["Word", "POS", "Review Date", "逾期天數"])
                    )
    except Exception:
        # 純文字整齊化輸出
        headers = [c.ljust(width_map[c]) for c in cols]
        sep = "-".join("".ljust(width_map[c], "-") for c in cols)
        print(" ".join(headers))
        print(sep)
        page_size = int(os.getenv("DUE_PAGE_SIZE", "20"))
        for chunk in _paginate(rows, page_size=page_size):
            for row in chunk:
                line = []
                for c in cols:
                    cell = _format_cell(row.get(c, ""), width_map[c])
                    if c in ("逾期天數",):
                        line.append(str(cell).rjust(width_map[c]))
                    else:
                        line.append(str(cell).ljust(width_map[c]))
                print(" ".join(line))
            print()

    # 可選：自動匯出 CSV（DUE_EXPORT=1 才會匯出；預設 1）
    if os.getenv("DUE_EXPORT", "1") == "1":
        out_dir = Path("data/backup")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"due_{as_of.strftime('%Y%m%d')}.csv"
        try:
            pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"💾 已匯出 CSV：{out_path}")
        except Exception as e:
            print(f"⚠ 匯出 CSV 失敗：{e}")

    print(f"\n共 {total} 筆需要複習。")
    pause()

# =========================
# 功能：設定下一次複習日（優化版）
# =========================
def action_schedule_next():
    header("設定下一次複習日")
    word = ask("要排程的 Word（完全一致比對）")
    if not word:
        print("⚠ 必填：Word")
        pause()
        return

    default_days = os.getenv("DEFAULT_REVIEW_DAYS", "3")
    hint = f"幾天後複習？（常用：1/3/7/14/30；預設 {default_days}）"
    raw = ask(hint, default_days).strip()
    try:
        days = int(raw)
        if days < 0:
            raise ValueError
    except ValueError:
        print("⚠ 天數需為非負整數；已改用預設 3 天")
        days = 3

    ok = schedule_next(word, days)
    if ok:
        next_day = date.today() + timedelta(days=days)
        print(f"✅ 已設定：{word} ➜ {next_day.isoformat()}（+{days} 天）")
    else:
        print("⚠ 找不到該單字")
    pause()

# =========================
# 功能：批量匯入
# =========================
def action_bulk_import():
    header("批量匯入 CSV")
    path = ask("CSV 路徑（例如 data/import_template.csv）")
    p = Path(path)
    if not p.exists():
        print("⚠ 找不到檔案")
        return
    try:
        n = bulk_import_csv(str(p))
        print(f"✅ 已匯入 {n} 筆")
    except Exception as e:
        print(f"❌ 匯入失敗：{e}")
    pause()

# =========================
# 功能：備份
# =========================
def action_backup():
    header("備份整張表為 CSV")
    out = ask("輸出檔名", f"backup_{date.today().isoformat()}.csv")
    backup_to_csv(out)
    print(f"💾 已備份 → {Path(out).resolve()}")
    pause()

# =========================
# 功能：檢視前 20 筆
# =========================
def action_peek_top():
    header("檢視前 20 筆資料")
    df = read_df()
    if df.empty:
        print("（表中尚無資料）")
        pause()
        return

    cols = [c for c in [
        "Word", "POS", "Meaning", "Example", "Synonyms", "Topic", "Source", "Review Date", "Note"
    ] if c in df.columns]
    sub = df[cols].head(20).fillna("")

    console = Console()
    table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)

    # 可依需求調整每欄最大寬度
    maxw = {
        "Word": 16, "POS": 6, "Meaning": 20, "Example": 48,
        "Synonyms": 28, "Topic": 16, "Source": 18, "Review Date": 12, "Note": 20
    }

    # 建欄：超出寬度自動換行（fold）
    for c in cols:
        table.add_column(
            c,
            no_wrap=False,
            overflow="fold",
            max_width=maxw.get(c, 20)
        )

    # 加列：把換行符移除避免意外斷行
    for _, row in sub.iterrows():
        cells = [str(row.get(c, "")).replace("\n", " ").strip() for c in cols]
        table.add_row(*cells)

    console.print(table)
    pause()

# =========================
# 功能：智慧新增（自動補）
# =========================
def action_smart_add():
    header("智慧新增（只輸入單字，其餘自動補）")
    word = ask("Word（英文單字）")
    if not word:
        print("⚠ 必填：Word")
        return

    auto = enrich_word(word, want_chinese=False)

    print("\n系統預填如下（可按 Enter 接受，或輸入覆蓋）：")
    auto["POS"] = ask("POS", auto.get("POS") or "n.")
    auto["Meaning"] = ask("Meaning", auto.get("Meaning"))
    auto["Example"] = ask("Example", auto.get("Example"))
    auto["Synonyms"] = ask("Synonyms", auto.get("Synonyms"))
    auto["Topic"] = ask("Topic", auto.get("Topic"))
    auto["Source"] = ask("Source", auto.get("Source"))
    auto["Review Date"] = ask("Review Date", date.today().isoformat())
    auto["Note"] = ask("Note", auto.get("Note"))

    added = add_word(auto)
    if added is False:
        print(f"⏩ 已跳過重複：{auto['Word']}")
    else:
        print(f"✅ 已新增：{auto['Word']}")
    pause()

# =========================
# 主選單
# =========================
def main_menu():
    open_ws()
    while True:
        header("IELTS Vocabulary Manager - 互動式選單")
        print("1) 智慧新增（只輸入單字，其餘自動補）")
        print("2) 新增單字（互動式輸入）")
        print("3) 查看今天到期要複習的單字")
        print("4) 設定某字的下一次複習日")
        print("5) 批量匯入 CSV（大量導入）")
        print("6) 備份整張表為 CSV")
        print("7) 檢視前 20 筆資料")
        print("0) 離開")
        choice = ask("請輸入選項編號", "1")
        if choice == "1":
            action_smart_add()
        elif choice == "2":
            action_add_word()
        elif choice == "3":
            action_due_reviews()
        elif choice == "4":
            action_schedule_next()
        elif choice == "5":
            action_bulk_import()
        elif choice == "6":
            action_backup()
        elif choice == "7":
            action_peek_top()
        elif choice == "0":
            print("👋 Bye!")
            break
        else:
            print("⚠ 無效選項，請再試一次。")
