from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import re
import requests

# 可選中文翻譯
try:
    from deep_translator import GoogleTranslator
    _has_translator = True
except Exception:
    _has_translator = False

# ====== 快取開關（預設關閉）======
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
# ====== 以上為快取設定 ======

def _fetch_dictionaryapi(word: str) -> Dict[str, Any]:
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return {"source": "dictionaryapi.dev", "data": r.json()}

def _fetch_datamuse_synonyms(word: str) -> List[str]:
    r = requests.get("https://api.datamuse.com/words", params={"rel_syn": word, "max": 20}, timeout=15)
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

# ---- Datamuse: 推斷常見詞性 ----
def _datamuse_preferred_pos(word: str) -> Optional[str]:
    """回傳 'noun'/'verb'/'adjective'/'adverb' 之一；失敗回 None。"""
    try:
        r = requests.get(
            "https://api.datamuse.com/words",
            params={"sp": word.lower(), "md": "p", "max": 1},
            timeout=10
        )
        if not r.ok:
            return None
        items = r.json() or []
        if not items:
            return None
        tags = items[0].get("tags") or []
        for t in tags:
            if t == "n":   return "noun"
            if t == "v":   return "verb"
            if t == "adj": return "adjective"
            if t == "adv": return "adverb"
    except Exception:
        pass
    return None

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
    full_map = {"noun": "n", "verb": "v", "adjective": "adj", "adverb": "adv",
                "preposition": "prep", "pronoun": "pron", "conjunction": "conj", "interjection": "interj"}
    if p in full_map:
        p = full_map[p]
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

def enrich_word(word: str, want_chinese: bool = True) -> Dict[str, Any]:
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
        try:
            payload["dictionaryapi"] = _fetch_dictionaryapi(word)
        except Exception as e:
            payload["dictionaryapi_error"] = str(e)
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

            # Datamuse 推斷詞性（優先）
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
        "POS": pos or "n.",
        "Meaning": meaning_out,
        "Example": example,
        "Synonyms": synonyms_pipe,
        "Topic": "",
        "Source": source,
        "Review Date": "",
        "Note": ""
    }
