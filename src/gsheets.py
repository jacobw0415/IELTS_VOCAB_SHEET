from __future__ import annotations
import os
import time
import random
from datetime import date, timedelta
from typing import Dict, Any, Optional, Tuple, Set, List
from pathlib import Path

import gspread
import pandas as pd
from dateutil.parser import parse as parse_dt
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ---------- 穩定載入 .env 與金鑰（無論在根目錄或 src 執行） ----------
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SHEET_URL = os.getenv("SHEET_URL")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Sheet1")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", str(ROOT / "service_account.json"))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 預期表頭（欄位順序）
EXPECTED_HEADERS = [
    "Word", "POS", "Meaning", "Example", "Synonyms",
    "Topic", "Source", "Review Date", "Note"
]

# =========================
# Google API 重試 / 退避
# =========================
def _retry_gsheet(max_tries: int = 5, base: float = 0.6, factor: float = 2.0, jitter: float = 0.2):
    """
    對 gspread 呼叫做指數退避重試。
    針對常見暫時性錯誤（429/5xx/rateLimitExceeded）會重試。
    """
    def deco(fn):
        def wrapper(*a, **kw):
            delay = base
            for i in range(1, max_tries + 1):
                try:
                    return fn(*a, **kw)
                except gspread.exceptions.APIError as e:
                    msg = str(e).lower()
                    retryable = any(x in msg for x in ("429", "500", "502", "503", "504", "ratelimit", "rate limit"))
                    if not retryable or i == max_tries:
                        raise
                    time.sleep(delay + random.random() * jitter)
                    delay *= factor
        return wrapper
    return deco

# =========================
# gspread client / spreadsheet / worksheet
# =========================
def _client() -> gspread.Client:
    if not Path(SERVICE_ACCOUNT_FILE).exists():
        raise FileNotFoundError(f"找不到金鑰檔：{SERVICE_ACCOUNT_FILE}")
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)

def open_spreadsheet() -> gspread.Spreadsheet:
    """開啟整本試算表。"""
    if not SHEET_URL:
        raise RuntimeError("SHEET_URL 未設定，請檢查 .env")
    gc = _client()
    return gc.open_by_url(SHEET_URL)

def ensure_headers(ws: gspread.Worksheet) -> None:
    """
    確保第 1 列為預期表頭：
    - 若第 1 列為空 → 直接寫表頭
    - 若第 1 列不是表頭 → 在第 1 列插入表頭（原資料下移）
    - 若欄數不足 → 直接覆蓋補齊
    """
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []

    norm_first = [c.strip().lower() for c in first_row]
    norm_expected = [c.lower() for c in EXPECTED_HEADERS]

    if len(first_row) == 0:
        ws.update("A1:I1", [EXPECTED_HEADERS], value_input_option="USER_ENTERED")
        return

    if norm_first[:len(norm_expected)] != norm_expected:
        ws.insert_row(EXPECTED_HEADERS, index=1)
        return

    if len(first_row) < len(EXPECTED_HEADERS):
        ws.update("A1:I1", [EXPECTED_HEADERS], value_input_option="USER_ENTERED")

def open_ws():
    """開啟主工作表；若分頁不存在則建立；並保證表頭正確。"""
    sh = open_spreadsheet()
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=10)
    ensure_headers(ws)
    return ws

# =========================
# Word+Meaning 去重快取
# =========================
_key_cache: Optional[Set[Tuple[str, str]]] = None  # (word_lower, meaning_lower)

def _normalize_key(word: str, meaning: str) -> Tuple[str, str]:
    return (str(word or "").strip().lower(), str(meaning or "").strip().lower())

def read_df() -> pd.DataFrame:
    ws = open_ws()
    return pd.DataFrame(ws.get_all_records())

def _build_key_cache(df: pd.DataFrame) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    if df is None or df.empty:
        return keys
    if not {"Word", "Meaning"} <= set(df.columns):
        return keys
    for _, row in df[["Word", "Meaning"]].fillna("").iterrows():
        keys.add(_normalize_key(row["Word"], row["Meaning"]))
    return keys

def refresh_key_cache() -> None:
    """重建去重快取（大量匯入前後可呼叫）。"""
    global _key_cache
    df = read_df()
    _key_cache = _build_key_cache(df)

def exists_word_meaning(word: str, meaning: str) -> bool:
    """快速判斷 Word+Meaning 是否已存在（大小寫不敏感）。"""
    global _key_cache
    if _key_cache is None:
        refresh_key_cache()
    return _normalize_key(word, meaning) in (_key_cache or set())

# =========================
# 基本資料存取（含重試）
# =========================
@_retry_gsheet()
def _append_row(ws: gspread.Worksheet, row: List[str]) -> None:
    ws.append_row(row, value_input_option="USER_ENTERED")

@_retry_gsheet()
def _append_rows(ws: gspread.Worksheet, rows: List[List[str]]) -> None:
    ws.append_rows(rows, value_input_option="USER_ENTERED")

@_retry_gsheet()
def _update_cell(ws: gspread.Worksheet, r: int, c: int, val: Any) -> None:
    ws.update_cell(r, c, val)

def add_word(row: Dict[str, Any]) -> Optional[bool]:
    """
    新增單字一筆（自動填入缺漏欄位）：
    - 以 Word+Meaning 去重（已存在 → 跳過並提示，回傳 False）
    - 自動補 Review Date（空值 → 今天）
    """
    ws = open_ws()

    # 去重：Word + Meaning
    word = row.get("Word", "")
    meaning = row.get("Meaning", "")
    if word and meaning and exists_word_meaning(word, meaning):
        print(f"⏩ Skip duplicate (Word+Meaning): {word} / {str(meaning)[:30]}…")
        return False

    ordered = [
        row.get("Word", ""),
        row.get("POS", ""),
        row.get("Meaning", ""),
        row.get("Example", ""),
        row.get("Synonyms", ""),
        row.get("Topic", ""),
        row.get("Source", ""),
        row.get("Review Date", date.today().isoformat()) or date.today().isoformat(),
        row.get("Note", ""),
    ]
    _append_row(ws, ordered)

    # 更新本地快取
    global _key_cache
    if _key_cache is None:
        _key_cache = set()
    _key_cache.add(_normalize_key(word, meaning))
    return True

def due_reviews(as_of: Optional[date] = None) -> pd.DataFrame:
    if as_of is None:
        as_of = date.today()
    df = read_df()
    if df.empty or "Review Date" not in df.columns:
        return pd.DataFrame(columns=["Word", "Meaning", "Review Date"])

    def _due(x) -> bool:
        try:
            return parse_dt(str(x)).date() <= as_of
        except Exception:
            return False

    return df[df["Review Date"].apply(_due)].copy()

def schedule_next(word: str, days: int = 3) -> bool:
    """將第一個符合的單字之 'Review Date' 往後推 days 天。"""
    ws = open_ws()
    cells = ws.findall(word, in_column=1)  # A 欄 = Word
    if not cells:
        return False
    row_idx = cells[0].row
    next_date = (date.today() + timedelta(days=days)).isoformat()
    _update_cell(ws, row_idx, 8, next_date)  # H 欄 = Review Date
    return True

def bulk_import_csv(csv_path: str) -> int:
    """
    批量匯入 CSV：
    - 基本清理與欄位正規化
    - 以 Word+Meaning 去重（含現有表與本批去重）
    - 失敗行跳過（維持穩定）
    """
    df = pd.read_csv(csv_path).fillna("")
    required = {"Word", "POS", "Meaning"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少欄位: {', '.join(sorted(missing))}")

    # 清理與正規化
    df["Word"] = df["Word"].astype(str).str.strip()
    df["POS"] = (
        df["POS"].astype(str).str.strip().str.lower()
        .str.replace("noun", "n.", regex=False)
        .str.replace("verb", "v.", regex=False)
        .str.replace("adjective", "adj.", regex=False)
        .str.replace("adverb", "adv.", regex=False)
    )
    if "Review Date" in df.columns:
        def _norm_date(x):
            try:
                return parse_dt(str(x)).date().isoformat()
            except Exception:
                return ""
        df["Review Date"] = df["Review Date"].apply(_norm_date)

    # 先用本批去重（Word+Meaning）
    if {"Word", "Meaning"} <= set(df.columns):
        df = df.drop_duplicates(subset=["Word", "Meaning"])

    # 讀現有表建立 key cache（僅一次）
    refresh_key_cache()

    # 過濾：排除（已存在於表）或（本批重複）
    to_append: List[List[str]] = []
    seen_batch: Set[Tuple[str, str]] = set()

    for _, r in df.iterrows():
        w = str(r.get("Word", "")).strip()
        m = str(r.get("Meaning", "")).strip()
        if not w or not m:
            continue  # 必要欄位缺漏，跳過

        key = _normalize_key(w, m)
        if key in seen_batch:
            continue
        if exists_word_meaning(w, m):
            continue

        seen_batch.add(key)
        ordered = [
            w,
            r.get("POS", ""),
            m,
            r.get("Example", ""),
            r.get("Synonyms", ""),
            r.get("Topic", ""),
            r.get("Source", ""),
            r.get("Review Date", "") or date.today().isoformat(),
            r.get("Note", ""),
        ]
        to_append.append(ordered)

    if not to_append:
        return 0

    sh = open_spreadsheet()
    ws = sh.worksheet(WORKSHEET_NAME)
    _append_rows(ws, to_append)

    # 匯入後更新快取
    for w, m in seen_batch:
        (_key_cache or set()).add((w, m))
    return len(to_append)

def backup_to_csv(output_path: str) -> None:
    df = read_df()
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ 已匯出至 {output_path}")

# =========================
# 視圖輸出到 Google Sheet 新分頁
# =========================
@_retry_gsheet()
def _clear_and_set(ws: gspread.Worksheet, headers: List[str], rows: List[List[str]]) -> None:
    ws.clear()
    if headers:
        ws.update(f"A1:{chr(64+len(headers))}1", [headers], value_input_option="USER_ENTERED")
    if rows:
        ws.update(f"A2:{chr(64+len(headers))}{len(rows)+1}", rows, value_input_option="USER_ENTERED")

def export_view_dataframe(df: pd.DataFrame, title: str) -> None:
    """
    將任意 DataFrame 以新分頁呈現在同一本試算表中。
    - 若分頁存在：清空後覆寫
    - 若不存在：建立後寫入
    """
    sh = open_spreadsheet()
    headers = list(df.columns)
    rows = df.fillna("").values.tolist()
    try:
        ws = sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=max(1000, len(rows)+10), cols=max(10, len(headers)+2))
    _clear_and_set(ws, headers, rows)
