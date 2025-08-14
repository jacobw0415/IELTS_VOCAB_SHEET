from datetime import date, datetime, timedelta
from pathlib import Path

# NEW: pandas 與 tabulate（tabulate 沒裝也能退回）
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box
from .enrich import enrich_word

from .gsheets import (
    add_word, read_df, due_reviews, schedule_next,
    bulk_import_csv, backup_to_csv, open_ws
)

# ----- 小工具 -----
def ask(prompt: str, default: str | None = None) -> str:
    tip = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{tip}: ").strip()
    return val if val else (default or "")

def ask_date(prompt: str, default: str | None = None) -> str:
    while True:
        v = ask(prompt, default)
        if not v:
            return ""
        try:
            if v.lower() in ("today", "t", "now"):   return date.today().isoformat()
            if v.lower() in ("tomorrow", "tmr"):     return (date.today()+timedelta(days=1)).isoformat()
            v = v.replace("/", "-")
            return datetime.fromisoformat(v).date().isoformat()
        except Exception:
            print("⚠ 日期格式請用 YYYY-MM-DD（或輸入 today / tomorrow）")

def pause(): input("\n(按 Enter 繼續) ")
def header(title: str):
    print("\n" + "="*60); print(title); print("="*60)

# ----- 功能 -----
def action_add_word():
    header("新增單字（互動式輸入）")
    word = ask("Word（英文單字）")
    if not word: print("⚠ 必填：Word"); return
    pos = ask("POS（詞性，如 n. / v. / adj.）", "n.")
    meaning = ask("Meaning（中文）")
    example = ask("Example（例句）")
    synonyms = ask("Synonyms（用 | 分隔）")
    topic = ask("Topic（主題）")
    source = ask("Source（出處）")
    review_date = ask_date("Review Date（YYYY-MM-DD / today / tomorrow）", date.today().isoformat())
    note = ask("Note（備註）")

    add_word({
        "Word": word, "POS": pos, "Meaning": meaning, "Example": example,
        "Synonyms": synonyms, "Topic": topic, "Source": source,
        "Review Date": review_date, "Note": note
    })
    print(f"✅ 已新增：{word}")
    pause()

def action_due_reviews():
    header("到期複習清單")
    as_of = ask_date("查詢日期（預設今天）", date.today().isoformat())
    df = due_reviews(date.fromisoformat(as_of))
    if df.empty:
        print("🎉 今天沒有到期要複習的單字")
    else:
        cols = [c for c in ["Word","Meaning","Example","Synonyms","Topic","Review Date"] if c in df.columns]
        print(df[cols].to_string(index=False, max_colwidth=40))
        print(f"\n共 {len(df)} 筆需要複習。")
    pause()

def action_schedule_next():
    header("設定下一次複習日")
    word = ask("要排程的 Word")
    if not word: print("⚠ 必填：Word"); return
    try: days = int(ask("幾天後複習？", "3"))
    except ValueError: days = 3
    ok = schedule_next(word, days)
    print("✅ 已設定" if ok else "⚠ 找不到該單字"); pause()

def action_bulk_import():
    header("批量匯入 CSV")
    path = ask("CSV 路徑（例如 data/import_template.csv）")
    p = Path(path)
    if not p.exists(): print("⚠ 找不到檔案"); return
    try:
        n = bulk_import_csv(str(p)); print(f"✅ 已匯入 {n} 筆")
    except Exception as e:
        print(f"❌ 匯入失敗：{e}")
    pause()

def action_backup():
    header("備份整張表為 CSV")
    out = ask("輸出檔名", f"backup_{date.today().isoformat()}.csv")
    backup_to_csv(out); print(f"💾 已備份 → {Path(out).resolve()}"); pause()

def action_peek_top():
    header("檢視前 20 筆資料")
    df = read_df()
    if df.empty:
        print("（表中尚無資料）")
        pause()
        return

    cols = [c for c in [
        "Word","POS","Meaning","Example","Synonyms","Topic","Source","Review Date","Note"
    ] if c in df.columns]
    sub = df[cols].head(20).fillna("")

    console = Console()
    table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)

    # 可依需求調整每欄最大寬度
    maxw = {
        "Word": 16, "POS": 6, "Meaning": 20, "Example": 48,
        "Synonyms": 28, "Topic": 16, "Source": 18, "Review Date": 12, "Note": 20
    }

    # 建欄：超出寬度自動換行（fold），必要時截斷（ellipsis）
    for c in cols:
        table.add_column(
            c,
            no_wrap=False,
            overflow="fold",         # 先嘗試換行
            max_width=maxw.get(c, 20)
        )

    # 加列：把換行符移除避免意外斷行
    for _, row in sub.iterrows():
        cells = [str(row.get(c, "")).replace("\n", " ").strip() for c in cols]
        table.add_row(*cells)

    console.print(table)
    pause()

def action_smart_add():
    header("智慧新增（只輸入單字，其餘自動補）")
    word = ask("Word（英文單字）")
    if not word:
        print("⚠ 必填：Word"); return

    auto = enrich_word(word, want_chinese=True)

    print("\n系統預填如下（可按 Enter 接受，或輸入覆蓋）：")
    auto["POS"]         = ask("POS",        auto["POS"] or "n.")
    auto["Meaning"]     = ask("Meaning",    auto["Meaning"])
    auto["Example"]     = ask("Example",    auto["Example"])
    auto["Synonyms"]    = ask("Synonyms",   auto["Synonyms"])
    auto["Topic"]       = ask("Topic",      auto["Topic"])
    auto["Source"]      = ask("Source",     auto["Source"])
    auto["Review Date"] = ask("Review Date", date.today().isoformat())
    auto["Note"]        = ask("Note",       auto["Note"])

    add_word(auto)
    print(f"✅ 已新增：{auto['Word']}")
    pause()
    
# ----- 主選單 -----
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
        if   choice == "1": action_smart_add()
        elif choice == "2": action_add_word()
        elif choice == "3": action_due_reviews()
        elif choice == "4": action_schedule_next()
        elif choice == "5": action_bulk_import()
        elif choice == "6": action_backup()
        elif choice == "7": action_peek_top()
        elif choice == "0": print("👋 Bye!"); break
        else: print("⚠ 無效選項，請再試一次。")
