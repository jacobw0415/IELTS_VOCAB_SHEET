import sys
import argparse
from datetime import date
from pathlib import Path

from .gsheets import add_word, bulk_import_csv, due_reviews, schedule_next, backup_to_csv

def build_parser():
    p = argparse.ArgumentParser(prog="vocab", description="IELTS vocab manager (Google Sheets)")
    sub = p.add_subparsers(dest="cmd")  # 不強制 required：無參數時可自動進 CLI

    # 互動式 CLI（你的入口）
    cli = sub.add_parser("cli", help="啟動互動式選單")
    cli_sub = cli.add_subparsers(dest="cli_cmd")
    cli_sub.add_parser("smart", help="智慧新增（只輸入單字，其餘自動補）")
    cli_sub.add_parser("add", help="直接進入『新增單字』表單")
    cli_sub.add_parser("menu", help="顯示完整選單（預設行為）")

    # 仍保留參數式子命令（自動化/排程用）
    a = sub.add_parser("add", help="（參數式）新增單字")
    a.add_argument("--word", required=True)
    a.add_argument("--pos", default="n.")
    a.add_argument("--meaning", required=True)
    a.add_argument("--example", default="")
    a.add_argument("--synonyms", default="")
    a.add_argument("--topic", default="")
    a.add_argument("--source", default="")
    a.add_argument("--review-date", default=date.today().isoformat())
    a.add_argument("--note", default="")

    im = sub.add_parser("import", help="批量匯入 CSV")
    im.add_argument("csv_path")

    d = sub.add_parser("due", help="列出到期複習")
    d.add_argument("--as-of", default=date.today().isoformat())

    s = sub.add_parser("schedule", help="設定下一次複習日")
    s.add_argument("--word", required=True)
    s.add_argument("--days", type=int, default=3)

    b = sub.add_parser("backup", help="備份成 CSV")
    b.add_argument("--out", default=f"backup_{date.today().isoformat()}.csv")

    # 🔎 建議新增：除錯專用（不寫入）
    dbg = sub.add_parser("debug-enrich", help="測試智慧補齊（不寫入，只印出欄位）")
    dbg.add_argument("--word", required=True)

    return p


def main():
    parser = build_parser()

    # 無任何參數 → 直接進互動式選單
    if len(sys.argv) == 1:
        from . import cli as ui
        return ui.main_menu()

    args = parser.parse_args()

    if args.cmd == "cli":
        from . import cli as ui
        if args.cli_cmd == "smart":
            return ui.action_smart_add()
        if args.cli_cmd == "add":
            return ui.action_add_word()
        return ui.main_menu()

    if args.cmd == "add":
        add_word({
            "Word": args.word, "POS": args.pos, "Meaning": args.meaning,
            "Example": args.example, "Synonyms": args.synonyms,
            "Topic": args.topic, "Source": args.source,
            "Review Date": args.review_date, "Note": args.note
        })
        print(f"✅ Added: {args.word}")

    elif args.cmd == "import":
        csv_path = Path(args.csv_path)
        if not csv_path.exists():
            print(f"⚠ 找不到檔案：{csv_path}")
        else:
            n = bulk_import_csv(str(csv_path))
            print(f"✅ Imported {n} rows")

    elif args.cmd == "due":
        try:
            as_of = date.fromisoformat(args.as_of)
        except Exception:
            print("⚠ 日期格式請用 YYYY-MM-DD，例如 2025-08-13；已改用今天。")
            as_of = date.today()
        df = due_reviews(as_of)
        if df.empty:
            print("🎉 今日無到期複習")
        else:
            cols = [c for c in ["Word","Meaning","Example","Synonyms","Topic","Review Date"] if c in df.columns]
            print(df[cols].to_string(index=False))

    elif args.cmd == "schedule":
        ok = schedule_next(args.word, args.days)
        print("✅ 下一次複習已設定" if ok else "⚠ 找不到該單字")

    elif args.cmd == "backup":
        out = Path(args.out)
        backup_to_csv(str(out))
        print(f"💾 Backup saved → {out.resolve()}")

    elif args.cmd == "debug-enrich":
        from .enrich import enrich_word  # 你已改回 dictionaryapi.dev
        data = enrich_word(args.word, want_chinese=True)
        from pprint import pprint
        pprint(data)


if __name__ == "__main__":
    main()
