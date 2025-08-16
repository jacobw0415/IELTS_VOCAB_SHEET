from datetime import date, datetime, timedelta
from pathlib import Path
from textwrap import shorten
import os
import sys
import re

# pandas 與 rich（已在專案中）
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box

from .enrich import enrich_word, predict_pos
from .gsheets import (
    add_word, read_df, due_reviews, schedule_next,
    bulk_import_csv, backup_to_csv, open_ws, export_view_dataframe
)

# =========================
# 拼字檢查（可選：pyspellchecker）
# =========================
_SPELL_AVAILABLE = False
try:
    from spellchecker import SpellChecker
    _spell = SpellChecker(language="en")
    _SPELL_AVAILABLE = True
except Exception:
    _spell = None  # 退回到基本檢查（只允許 a-z）

_word_re = re.compile(r"^[A-Za-z][A-Za-z\-]*$")  # 基本過濾：英文字母，允許連字號

def _basic_word_ok(word: str) -> bool:
    """基本檢查：只允許英文字母與可選連字號"""
    if not word:
        return False
    return bool(_word_re.fullmatch(word.strip()))

def is_valid_word(word: str) -> bool:
    """
    驗證單字是否合理：
    - 若有 pyspellchecker：用 unknown() 驗證是否為已知字
    - 否則：用基本規則檢查（僅 a-z/-）
    """
    w = (word or "").strip()
    if not _basic_word_ok(w):
        return False
    if _SPELL_AVAILABLE:
        try:
            # unknown() 回傳集合；若為空集合代表都是已知字
            return len(_spell.unknown([w.lower()])) == 0
        except Exception:
            # 安全退回
            return True
    return True  # 無拼字庫時，只做基本字元驗證

def suggest_words(word: str, limit: int = 3) -> list[str]:
    """
    提議近似拼法（需要 pyspellchecker）。
    排序策略：
      1) 先把 correction()（最佳校正）放第一
      2) 再依 word_probability 由高到低
      3) 同分時以長度差距較小者優先
    無庫或異常時回傳空清單。
    """
    if not _SPELL_AVAILABLE:
        return []
    try:
        w = (word or "").strip().lower()
        if not w:
            return []
        cands = list(_spell.candidates(w))
        if not cands:
            return []
        # 過濾一些太怪的項（非純字母/連字號），以及與原字相同者
        cands = [c for c in cands if _basic_word_ok(c)]
        cands = [c for c in cands if c != w]
        # 以詞頻機率排序；同分用長度差替代距離
        cands_sorted = sorted(
            cands,
            key=lambda x: (-_spell.word_probability(x), abs(len(x) - len(w)))
        )
        # 把最佳校正放到最前面（若存在於候選）
        best = _spell.correction(w)
        if best and best in cands_sorted:
            cands_sorted.remove(best)
            cands_sorted.insert(0, best)
        return cands_sorted[:limit]
    except Exception:
        return []

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

    # 先驗證單字（錯誤則拒絕並給建議）
    if not is_valid_word(word):
        tips = suggest_words(word, limit=3)
        if tips:
            print(f"❌ 單字拼寫錯誤或不存在：{word}\n   你是不是想輸入：{', '.join(tips)}")
        else:
            print(f"❌ 單字拼寫錯誤或不存在：{word}")
            if not _SPELL_AVAILABLE:
                print("  （建議安裝：pip install pyspellchecker 以獲得更精準的檢測與建議）")
        pause()
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
    if added is False:
        print(f"⏩ 已跳過重複：{word}")
    else:
        print(f"✅ 已新增：{word}")
    pause()

# =========================
# 內部：生成到期複習視圖（回傳排序後 DataFrame 與列印）
# =========================
def _build_and_show_due(as_of: date, sort_mode: str) -> pd.DataFrame:
    df = due_reviews(as_of)
    if df.empty:
        print("🎉 今天沒有到期要複習的單字")
        return df

    # 逾期天數
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

    # 欄位
    pref_cols = ["Word", "POS", "Meaning", "Example", "Synonyms", "Topic", "Review Date", "逾期天數"]
    cols = [c for c in pref_cols if c in df.columns]
    if "逾期天數" not in cols and "逾期天數" in df.columns:
        cols.append("逾期天數")

    # 排序
    if sort_mode == "pos" and "POS" in df.columns:
        sort_by = ["POS", "逾期天數", "Word"]
        ascending = [True, False, True]
        df_sorted = df.sort_values(by=sort_by, ascending=ascending, na_position="last")
        sort_label = "詞性分組（POS）"
    else:
        sort_by = ["逾期天數", "Topic", "Word"]
        ascending = [False, True, True]
        df_sorted = df.sort_values(by=sort_by, ascending=ascending, na_position="last")
        sort_label = "依逾期（Date）"

    total = len(df_sorted)
    print(f"📅 截止：{as_of.isoformat()}　需複習：{total} 筆　🔎 排序：{sort_label}\n")

    # 漂亮表格
    width_map = {
        "Word": 18, "POS": 6, "Meaning": 36, "Example": 36,
        "Synonyms": 24, "Topic": 12, "Review Date": 12, "逾期天數": 6
    }
    cols = [c for c in cols if c in df_sorted.columns]
    rows = df_sorted[cols].fillna("").to_dict("records")
    try:
        console = Console()
        table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)
        for c in cols:
            table.add_column(c, max_width=width_map.get(c, 20), overflow="fold",
                             no_wrap=(c in ["Word", "POS", "Review Date", "逾期天數"]))
        page_size = int(os.getenv("DUE_PAGE_SIZE", "20"))
        shown = 0
        for chunk in _paginate(rows, page_size=page_size):
            for row in chunk:
                table.add_row(*[str(row.get(c, "")) for c in cols])
            console.print(table)
            shown += len(chunk)
            if shown < total:
                table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)
                for c in cols:
                    table.add_column(c, max_width=width_map.get(c, 20), overflow="fold",
                                     no_wrap=(c in ["Word", "POS", "Review Date", "逾期天數"]))
    except Exception:
        # 純文字
        headers = [c.ljust(width_map[c]) for c in cols]
        sep = "-".join("".ljust(width_map[c], "-") for c in cols)
        print(" ".join(headers)); print(sep)
        page_size = int(os.getenv("DUE_PAGE_SIZE", "20"))
        for chunk in _paginate(rows, page_size=page_size):
            for row in chunk:
                line = []
                for c in cols:
                    cell = str(row.get(c, ""))
                    if c in ("逾期天數",):
                        line.append(cell.rjust(width_map[c]))
                    else:
                        line.append(cell.ljust(width_map[c]))
                print(" ".join(line))
            print()

    # CSV 匯出（可關閉）
    if os.getenv("DUE_EXPORT", "1") == "1":
        out_dir = Path("data/backup"); out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"due_{as_of.strftime('%Y%m%d')}_{sort_mode}.csv"
        try:
            df_sorted[cols].to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"💾 已匯出 CSV：{out_path}")
        except Exception as e:
            print(f"⚠ 匯出 CSV 失敗：{e}")

    return df_sorted[cols].copy()

# =========================
# 功能：到期複習清單（依逾期 / 依詞性）
# =========================
def action_due_reviews_date():
    header("到期複習清單（依逾期）")
    raw = ask_date("查詢日期（留空=今天；today/tomorrow 也可）", "")
    try:
        as_of = _parse_date_str(raw)
    except ValueError as e:
        print(e); pause(); return

    df_view = _build_and_show_due(as_of, sort_mode="date")
    if not df_view.empty:
        title = f"Due_{as_of.isoformat()}_date"
        export_view_dataframe(df_view, title)
        print(f"📤 已同步 Google Sheet 分頁：{title}")
    pause()

def action_due_reviews_pos():
    header("到期複習清單（依詞性）")
    raw = ask_date("查詢日期（留空=今天；today/tomorrow 也可）", "")
    try:
        as_of = _parse_date_str(raw)
    except ValueError as e:
        print(e); pause(); return

    df_view = _build_and_show_due(as_of, sort_mode="pos")
    if not df_view.empty:
        title = f"Due_{as_of.isoformat()}_pos"
        export_view_dataframe(df_view, title)
        print(f"📤 已同步 Google Sheet 分頁：{title}")
    pause()

# =========================
# 功能：設定下一次複習日（優化版）
# =========================
def action_schedule_next():
    header("設定下一次複習日")
    word = ask("要排程的 Word（完全一致比對）")
    if not word:
        print("⚠ 必填：Word"); pause(); return

    default_days = os.getenv("DEFAULT_REVIEW_DAYS", "3")
    raw = ask(f"幾天後複習？（常用：1/3/7/14/30；預設 {default_days}）", default_days).strip()
    try:
        days = int(raw); 
        if days < 0: raise ValueError
    except ValueError:
        print("⚠ 天數需為非負整數；已改用預設 3 天"); days = 3

    ok = schedule_next(word, days)
    if ok:
        next_day = date.today() + timedelta(days=days)
        print(f"✅ 已設定：{word} ➜ {next_day.isoformat()}（+{days} 天）")
    else:
        print("⚠ 找不到該單字")
    pause()

# =========================
# 功能：批量匯入 / 備份 / 檢視
# =========================
def action_bulk_import():
    header("批量匯入 CSV")
    path = ask("CSV 路徑（例如 data/import_template.csv）")
    p = Path(path)
    if not p.exists():
        print("⚠ 找不到檔案"); return
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
        print("（表中尚無資料）"); pause(); return

    cols = [c for c in ["Word","POS","Meaning","Example","Synonyms","Topic","Source","Review Date","Note"] if c in df.columns]
    sub = df[cols].head(20).fillna("")

    console = Console(); table = Table(box=box.SIMPLE_HEAVY, show_lines=False, expand=False)
    maxw = {"Word":16,"POS":6,"Meaning":20,"Example":48,"Synonyms":28,"Topic":16,"Source":18,"Review Date":12,"Note":20}
    for c in cols:
        table.add_column(c, no_wrap=False, overflow="fold", max_width=maxw.get(c,20))
    for _, row in sub.iterrows():
        cells = [str(row.get(c, "")).replace("\n"," ").strip() for c in cols]
        table.add_row(*cells)
    console.print(table); pause()

# =========================
# 功能：智慧新增（自動補）
# =========================
def action_smart_add():
    header("智慧新增（只輸入單字，其餘自動補）")
    word = ask("Word（英文單字）")
    if not word:
        print("⚠ 必填：Word"); return

    # 先驗證單字（錯誤則拒絕並給建議）
    if not is_valid_word(word):
        tips = suggest_words(word, limit=3)
        if tips:
            print(f"❌ 單字拼寫錯誤或不存在：{word}\n   你是不是想輸入：{', '.join(tips)}")
        else:
            print(f"❌ 單字拼寫錯誤或不存在：{word}")
            if not _SPELL_AVAILABLE:
                print("  （建議安裝：pip install pyspellchecker 以獲得更精準的檢測與建議）")
        pause()
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
        print("3) 查看到期清單（依逾期）→ 並同步到 Google Sheet 分頁")
        print("4) 查看到期清單（依詞性）→ 並同步到 Google Sheet 分頁")
        print("5) 設定某字的下一次複習日")
        print("6) 批量匯入 CSV（大量導入）")
        print("7) 備份整張表為 CSV")
        print("8) 檢視前 20 筆資料")
        print("0) 離開")
        choice = ask("請輸入選項編號", "1")
        if choice == "1":
            action_smart_add()
        elif choice == "2":
            action_add_word()
        elif choice == "3":
            action_due_reviews_date()
        elif choice == "4":
            action_due_reviews_pos()
        elif choice == "5":
            action_schedule_next()
        elif choice == "6":
            action_bulk_import()
        elif choice == "7":
            action_backup()
        elif choice == "8":
            action_peek_top()
        elif choice == "0":
            print("👋 Bye!")
            break
        else:
            print("⚠ 無效選項，請再試一次。")
