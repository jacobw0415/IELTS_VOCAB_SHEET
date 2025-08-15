from __future__ import annotations
import os
import json
import time
import random
from pathlib import Path
from typing import Dict, Any, List, Optional
import re
import requests

# =========================
# 可選中文翻譯
# =========================
try:
    from deep_translator import GoogleTranslator
    _has_translator = True
except Exception:
    _has_translator = False

# =========================
# 快取設定（預設關閉）
# =========================
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "0").lower() in ("1", "true", "yes")
CACHE_DIR = Path("data/cache")
if CACHE_ENABLED:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_load(word: str) -> Optional[Dict[str, Any]]:
    if not CACHE_ENABLED:
        return None
    p = CACHE_DIR / f"{word.lower()}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _cache_save(word: str, payload: Dict[str, Any]) -> None:
    if not CACHE_ENABLED:
        return
    p = CACHE_DIR / f"{word.lower()}.json"
    try:
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

# =========================
# 重試與退避（HTTP 專用）
# =========================
def _retry_http(
    max_tries: int = 5,
    base: float = 0.6,
    factor: float = 2.0,
    jitter: float = 0.2,
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504),
):
    """
    輕量級 HTTP 重試裝飾器：
    - 對 429/5xx、自訂例外情境退避重試
    - 指數退避 + 抖動，避免雪崩
    """
    def deco(fn):
        def wrapper(*a, **kw):
            delay = base
            for i in range(1, max_tries + 1):
                try:
                    resp: requests.Response = fn(*a, **kw)
                    if isinstance(resp, requests.Response):
                        if resp.status_code in retry_on_status:
                            raise requests.HTTPError(
                                f"retryable status {resp.status_code}", response=resp
                            )
                    return resp
                except (requests.Timeout, requests.ConnectionError, requests.HTTPError, requests.RequestException):
                    if i == max_tries:
                        raise
                    time.sleep(delay + random.random() * jitter)
                    delay *= factor
        return wrapper
    return deco

# =========================
# 常量 & 映射
# =========================
_DATAMUSE = "https://api.datamuse.com/words"
_DICTAPI = "https://api.dictionaryapi.dev/api/v2/entries/en/"

# 詞性正規化
_POS_MAP_CANON = {
    "n": "n.", "v": "v.", "adj": "adj.", "adv": "adv.",
    "noun": "n.", "verb": "v.", "adjective": "adj.", "adverb": "adv.",
    "preposition": "prep.", "pronoun": "pron.", "conjunction": "conj.", "interjection": "interj."
}

# =========================
# 外部請求（帶重試）
# =========================
_DEFAULT_HEADERS = {
    "User-Agent": "IELTS-Vocab-Manager/1.0 (+https://example.local)"
}

@_retry_http()
def _get(url: str, *, params: dict | None = None, timeout: float = 15.0) -> requests.Response:
    return requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout)

def _fetch_dictionaryapi(word: str) -> Dict[str, Any]:
    r = _get(_DICTAPI + word, timeout=15)
    r.raise_for_status()
    return {"source": "dictionaryapi.dev", "data": r.json()}

def _fetch_datamuse_synonyms(word: str) -> List[str]:
    r = _get(_DATAMUSE, params={"rel_syn": word, "max": 20}, timeout=10)
    if not r.ok:
        return []
    raw = [x.get("word", "") for x in r.json() if "word" in x]
    # 只保留純字詞，過濾掉奇怪符號/片語
    keep = []
    for w in raw:
        w = w.strip()
        if re.fullmatch(r"[A-Za-z\-]+", w):
            keep.append(w.lower())
    # 去重保序
    seen = set()
    out = []
    for w in keep:
        if w not in seen:
            out.append(w); seen.add(w)
    return out[:8]

# ---- Datamuse: 推斷常見詞性（帶重試）----
def _datamuse_preferred_pos(word: str) -> Optional[str]:
    """
    回傳 'noun'/'verb'/'adjective'/'adverb' 之一；失敗回 None。
    先用 Datamuse md=p；Datamuse 無結果再回退到 dictionaryapi.dev 解析。
    """
    try:
        r = _get(_DATAMUSE, params={"sp": word.lower(), "md": "p", "max": 1}, timeout=10)
        if r.ok:
            items = r.json() or []
            if items:
                tags = items[0].get("tags") or []
                for t in tags:
                    if t == "n":   return "noun"
                    if t == "v":   return "verb"
                    if t == "adj": return "adjective"
                    if t == "adv": return "adverb"
    except Exception:
        pass

    # Fallback: 從 dictionaryapi.dev 嘗試讀第一個 meanings 的 partOfSpeech
    try:
        r2 = _get(_DICTAPI + word, timeout=10)
        if r2.ok:
            payload = r2.json()
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                meanings = payload[0].get("meanings") or []
                for m in meanings:
                    pos = (m.get("partOfSpeech") or "").lower()
                    if pos in ("noun", "verb", "adjective", "adverb"):
                        return pos
    except Exception:
        pass
    return None

# =========================
# 定義挑選與正規化
# =========================
def _pick_best_sense(meanings: list, preferred_pos: Optional[str] = None) -> Dict[str, Any]:
    """
    先以 Datamuse 推斷的詞性為主；若找不到該詞性，
    再依 dictionaryapi.dev 的原始順序選第一個有例句的定義，
    否則就選第一個定義。
    """
    if not meanings:
        return {}

    def pick_from_meaning(m) -> Optional[Dict[str, Any]]:
        pos = (m.get("partOfSpeech") or "").lower()
        defs = m.get("definitions") or []
        if not defs:
            return None
        # 優先帶 example
        for d in defs:
            if d.get("definition"):
                if d.get("example"):
                    return {"pos": pos, "def": d["definition"], "example": d["example"]}
        # 否則第一個定義
        d0 = defs[0]
        if d0.get("definition"):
            return {"pos": pos, "def": d0["definition"], "example": d0.get("example", "")}
        return None

    # 1) 先找 preferred_pos
    if preferred_pos:
        for m in meanings:
            pos = (m.get("partOfSpeech") or "").lower()
            if pos == preferred_pos:
                picked = pick_from_meaning(m)
                if picked:
                    return picked

    # 2) 回退
    for m in meanings:
        picked = pick_from_meaning(m)
        if picked:
            return picked
    return {}

def _norm_pos(pos: str) -> str:
    p = (pos or "").lower().strip().strip(".")
    canon = _POS_MAP_CANON.get(p)
    if canon:
        return canon
    # 若已經是短寫（n/v/adj/adv 等），補上句點
    if p in {"n", "v", "adj", "adv", "prep", "pron", "conj", "interj"}:
        return p + "."
    return pos or ""

def _translate_or_fallback(en_text: str) -> str:
    """翻譯失敗就退回英文釋義，不讓 Meaning 變空白。"""
    en_text = (en_text or "").strip()
    if not en_text:
        return ""
    if _has_translator:
        try:
            zh = GoogleTranslator(source="auto", target="zh-TW").translate(en_text)
            zh = (zh or "").strip()
            if zh:
                return zh
        except Exception:
            pass
    return en_text  # 回退英文

# =========================
# 封裝：對外 API
# =========================
def predict_pos(word: str) -> Optional[str]:
    """
    對外提供的「自動詞性分類」API。
    回傳正規化後的 POS（n./v./adj./adv. ...），或 None。
    """
    pos = _datamuse_preferred_pos(word)
    if pos:
        return _norm_pos(pos)
    return None

def enrich_word(word: str, want_chinese: bool = True) -> Dict[str, Any]:
    """
    核心補齊：
    - 自動詞性分類（Datamuse → dictionaryapi.dev fallback）
    - dictionaryapi.dev 取定義/例句
    - Datamuse 取同義詞
    - 快取可開關（CACHE_ENABLED）
    - 翻譯失敗回英文，避免 Meaning 空白
    """
    word = (word or "").strip()
    if not word:
        raise ValueError("Empty word")

    pos = ""
    meaning_en = ""
    example = ""
    source = "auto"

    # 取得/快取
    cached = _cache_load(word)
    if cached:
        payload = cached
    else:
        payload = {}
        # 字典主來源
        try:
            payload["dictionaryapi"] = _fetch_dictionaryapi(word)
        except Exception as e:
            payload["dictionaryapi_error"] = str(e)
        # 同義詞
        try:
            payload["synonyms"] = _fetch_datamuse_synonyms(word)
        except Exception as e:
            payload["synonyms_error"] = str(e)
        _cache_save(word, payload)

    # 解析 dictionaryapi.dev 結果
    try:
        data = payload.get("dictionaryapi", {}).get("data", [])
        if isinstance(data, list) and data and isinstance(data[0], dict):
            entry = data[0]

            # Datamuse 推斷詞性（優先）→ fallback dictionaryapi.dev
            preferred_pos = _datamuse_preferred_pos(word)
            best = _pick_best_sense(entry.get("meanings") or [], preferred_pos=preferred_pos)
            pos = _norm_pos(best.get("pos", ""))
            meaning_en = (best.get("def", "") or "").strip()
            example = (best.get("example", "") or "").strip()
            source = "dictionaryapi.dev"
    except Exception:
        pass

    # 同義詞（用 " | " 分隔，便於閱讀）
    synonyms = payload.get("synonyms") or []
    synonyms_pipe = " | ".join(synonyms) if synonyms else ""

    # 中文/英文釋義輸出（翻譯失敗就回英文）
    meaning_out = _translate_or_fallback(meaning_en) if want_chinese else (meaning_en or "")

    return {
        "Word": word,
        "POS": pos or (predict_pos(word) or "n."),  # 再以 predict_pos 補一次，保底
        "Meaning": meaning_out,
        "Example": example,
        "Synonyms": synonyms_pipe,
        "Topic": "",
        "Source": source,
        "Review Date": "",
        "Note": ""
    }
