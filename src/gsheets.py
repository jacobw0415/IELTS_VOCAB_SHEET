from __future__ import annotations
import os
from datetime import date, timedelta
from typing import Dict, Any, Optional
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


def _client() -> gspread.Client:
    if not Path(SERVICE_ACCOUNT_FILE).exists():
        raise FileNotFoundError(f"找不到金鑰檔：{SERVICE_ACCOUNT_FILE}")
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


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
    """開啟工作表；若分頁不存在則建立；並保證表頭正確。"""
    if not SHEET_URL:
        raise RuntimeError("SHEET_URL 未設定，請檢查 .env")
    gc = _client()
    sh = gc.open_by_url(SHEET_URL)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=10)

    ensure_headers(ws)
    return ws


def add_word(row: Dict[str, Any]) -> None:
    """新增單字一筆（自動填入缺漏欄位）。"""
    ws = open_ws()
    ordered = [
        row.get("Word", ""),
        row.get("POS", ""),
        row.get("Meaning", ""),
        row.get("Example", ""),
        row.get("Synonyms", ""),
        row.get("Topic", ""),
        row.get("Source", ""),
        row.get("Review Date", date.today().isoformat()),
        row.get("Note", ""),
    ]
    ws.append_row(ordered, value_input_option="USER_ENTERED")


def read_df() -> pd.DataFrame:
    ws = open_ws()
    return pd.DataFrame(ws.get_all_records())


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
    ws.update_cell(row_idx, 8, next_date)  # H 欄 = Review Date
    return True


def bulk_import_csv(csv_path: str) -> int:
    """批量匯入 CSV；做基本清理並以 Word+Meaning 去重。"""
    df = pd.read_csv(csv_path).fillna("")
    required = {"Word", "POS", "Meaning"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少欄位: {', '.join(sorted(missing))}")

    df["Word"] = df["Word"].astype(str).str.strip()
    df["POS"] = (
        df["POS"].astype(str).str.strip().str.lower()
        .str.replace("noun", "n.", regex=False)
        .str.replace("verb", "v.", regex=False)
    )

    if "Review Date" in df.columns:
        def _norm_date(x):
            try:
                return parse_dt(str(x)).date().isoformat()
            except Exception:
                return ""
        df["Review Date"] = df["Review Date"].apply(_norm_date)

    if {"Word", "Meaning"} <= set(df.columns):
        df = df.drop_duplicates(subset=["Word", "Meaning"])

    ws = open_ws()
    existing = read_df()
    if not existing.empty:
        key = ["Word", "Meaning"]
        common = [k for k in key if k in existing.columns and k in df.columns]
        if common:
            merged = df.merge(existing[common], on=common, how="left", indicator=True)
            df = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])

    rows = df.reindex(columns=EXPECTED_HEADERS, fill_value="").values.tolist()
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def backup_to_csv(output_path: str) -> None:
    df = read_df()
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ 已匯出至 {output_path}")