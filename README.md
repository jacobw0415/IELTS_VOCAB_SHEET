# IELTS Vocab Sheet — README

用 Google Sheets + Python 打造的**系統化英文單字學習**與**複習排程**工具。  
支援互動式 CLI（逐欄位輸入）、批量匯入、到期複習清單、備份，以及自動維護表頭。

---

## 功能一覽

1. **新增單字**：寫入 Google Sheet（自動補齊欄位、維持欄位順序）。  
2. **批量匯入 CSV**：先清理/去重，再一次寫入。  
3. **到期複習清單**：依 `Review Date` 篩出今天需複習的單字。  
4. **設定下一次複習日**：把某字的 `Review Date` 往後推 N 天。  
5. **備份**：將整張表導出為 CSV（每日備份可排程）。  
6. **自動表頭維護**：無論表內已有資料與否，都保證第 1 列是  
   `Word | POS | Meaning | Example | Synonyms | Topic | Source | Review Date | Note`。

---

## 使用到的技術

- **Python 3.10+**
- **gspread**（Google Sheets API 的 Python 封裝）
- **google-auth**（Service Account 驗證）
- **pandas**（資料處理）
- **python-dateutil**（日期解析/正規化）
- **python-dotenv**（載入 `.env` 設定）
- **Google Sheets API**（在 Google Cloud Console 啟用）

---

## 專案結構

```
ielts_vocab_sheet/
├─ .env                      # 你的環境設定（Sheet URL、分頁名、金鑰路徑）
├─ service_account.json      # Google 服務帳戶金鑰（請勿上傳 Git）
├─ requirements.txt
├─ data/
│  └─ import_template.csv    # 批量匯入的欄位範本
└─ src/
   ├─ __init__.py
   ├─ gsheets.py             # 與 Google Sheets 溝通 + 清理/表頭維護
   ├─ main.py                # 入口：參數式 CLI + 互動式 CLI 子命令
   └─ cli.py                 # 互動式選單（逐欄位輸入）
```

---

## 事前準備（Google Cloud 設定）

1. 到 **Google Cloud Console** 建立專案。  
2. 啟用 **Google Sheets API**。  
3. 建立 **Service Account** → 產生 **JSON 金鑰**並下載（檔名如 `service_account.json`）。  
4. 打開你的 Google Sheet（建議命名如 *vocabulary for IELTS*），按「**分享**」，把 **Service Account 的 `client_email`** 加入為**編輯者**。  
5. 將下載的 `service_account.json` 放到專案根目錄（與 `.env` 同層）。

> ⚠️ `service_account.json` 與 `.env` 都**不要**上傳到 Git（已在 `.gitignore`）。

---

## 安裝與環境

### 1) 建立虛擬環境並安裝套件
```bash
# 進到專案根目錄
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

**requirements.txt**
```
gspread==6.1.2
google-auth==2.34.0
pandas==2.2.2
python-dateutil==2.9.0.post0
python-dotenv==1.0.1
```

### 2) 建立 `.env`
```ini
# 你的試算表完整 URL（從地址列複製）
SHEET_URL=https://docs.google.com/spreadsheets/d/<你的ID>/edit
# 要操作的分頁名稱（需與試算表底部 tab 名一致）
WORKSHEET_NAME=Sheet1
# 服務帳戶金鑰檔路徑（預設放在專案根）
SERVICE_ACCOUNT_FILE=service_account.json
```

> 若你想把金鑰內容放到雲端環境變數，也支援 `from_service_account_info` 的寫法（可再告知我幫你改）。

---

## 執行方式

> **請在專案根目錄執行**（不是 `src` 目錄）。

### A) 互動式 CLI（推薦日常使用）
```bash
python -m src.main              # 無參數 → 直接進選單
# 或
python -m src.main cli          # 顯示完整選單
python -m src.main cli add      # 直接進入「新增單字」表單
```
在選單中可選：
- 新增單字（逐欄位輸入）
- 查看今天到期要複習的單字
- 設定某字的下一次複習日
- 批量匯入 CSV
- 備份整張表為 CSV
- 檢視前 20 筆資料

### B) 參數式 CLI（適合自動化/排程）
```bash
# 新增單字
python -m src.main add --word mitigate --pos v. --meaning "減輕；緩和"   --example "Policies are needed to mitigate climate change."   --synonyms "alleviate|ease" --topic Environment --source "Cambridge 17"

# 查看到期複習（預設今天）
python -m src.main due

# 設定下一次複習日（把某字往後 7 天）
python -m src.main schedule --word mitigate --days 7

# 批量匯入 CSV
python -m src.main import data/import_template.csv

# 備份
python -m src.main backup --out backup_2025-08-12.csv
```

---

## Google Sheet 欄位設計

表頭（第 1 列）會由程式**自動維護**為：

```
Word | POS | Meaning | Example | Synonyms | Topic | Source | Review Date | Note
```

> 若你的第一列原本是資料，程式會自動把表頭**插入到第 1 列**，原資料整列下移，確保欄位一致。

---

## CSV 匯入格式

`data/import_template.csv` 範例：
```csv
Word,POS,Meaning,Example,Synonyms,Topic,Source,Review Date,Note
mitigate,v.,減輕；緩和,Policies are needed to mitigate climate change.,alleviate|ease,Environment,Cambridge 17,2025-08-20,
allocate,v.,分配,We should allocate more funds to education.,assign|distribute,Education,BBC,2025-08-20,
```

匯入時會進行：
- 基本清理（去前後空白、`POS` 正規化、`Review Date` 轉 ISO）
- **去重**（以 `Word+Meaning` 比對；不重覆寫入）
- 欄位對齊（缺少欄位以空字串補齊）

---

## 常見問題（Troubleshooting）

- **`FileNotFoundError: service_account.json`**  
  你可能在 `src` 目錄執行。請回到專案根；或將 `.env` 的 `SERVICE_ACCOUNT_FILE` 設為 `../service_account.json`。  
  我們的程式已加入「根目錄優先」的路徑尋找，照 README 執行通常不會遇到。

- **終端顯示 Added，但表單沒變**  
  90% 是 `.env` 的 `SHEET_URL` 或 `WORKSHEET_NAME` 指到**不同的檔/分頁**。  
  請重新複製你正在看的那份試算表 URL，並確認分頁名一致（大小寫一致）。

- **權限錯誤（403）**  
  忘了在 Google Sheet「分享」給 **Service Account 的 `client_email`**，權限需為**編輯者**。

- **PowerShell 多行命令錯誤**  
  Windows PowerShell 的續行符號是 **反引號** `` ` ``，不是 `\`。或把命令寫成同一行。

---

## 學習流程建議（IELTS）

1. **收集與新增**：看到新字 → 用互動式 CLI 逐欄位輸入（帶例句/主題/來源）。  
2. **每日複習**：執行 `due` 或在選單看「到期複習清單」，完成後用 `schedule` 推遲下次日期。  
3. **每週備份**：執行 `backup`，形成學習歷程。  
4. **大量導入**：遇到老師/同學給的詞表 → 整理成 CSV → `import` 一次導入。

---

## 版控與安全

- `.gitignore` 已忽略 `service_account.json` 與 `.env`。  
- 請勿將金鑰或試算表敏感網址提交到公共版本庫。  
- 若要雲端部署，建議用環境變數存放金鑰內容（可提供對應程式改法）。

---

## 待辦 / 可選擴充

- `Mastery (0-5)` 熟練度 + 動態排程（1/3/7/14/30 天）。  
- `quiz` 模式：從 `Example/Meaning` 出題，答題即時回寫熟練度與下次複習日。  
- 導出 Anki/Quizlet 格式，或反向同步。
