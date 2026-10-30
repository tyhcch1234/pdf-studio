# PDF Studio

## 只要一個檔案：`pdf_studio.py`

下載這個檔案就能用，不必整個資料夾、不需要網頁：

```bash
pip install pymupdf python-docx numpy pillow rapidocr-onnxruntime onnxruntime
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install pix2tex latex2mathml mathml2omml

python3 pdf_studio.py              # 開視窗（三個功能）
python3 pdf_studio.py --check      # 只檢查環境
python3 pdf_studio.py a.pdf b.pdf  # 直接把 PDF 轉成 Word
```

用的是完整 Python 管線：RapidOCR 做 OCR、pix2tex 辨識數式、python-docx 產生
**真正的 Word 方程式**，圖片與閱讀順序也會保留。

`pdf_studio.py` 由 `python3 build_single.py` 從 `pipeline/` 自動產生，改動請改前者。


Local web app with three PDF tools:

| 工具 | 說明 | 處理位置 |
|---|---|---|
| **PDF → Word** | 文字轉為可編輯文字、圖片原樣保留、數式轉成 Word 方程式（OMML，可用方程式編輯器修改） | 本機 Python 服務 |
| **合併 PDF** | 多檔依序合併，可調順序與頁碼範圍 | 瀏覽器（不上傳） |
| **A3 → A4** | 沿頁面中線切成兩張 A4，支援橫向／直向與旋轉頁面 | 瀏覽器（不上傳） |

## 使用方式（本機執行，不需要網頁或伺服器）

```bash
pip install -r requirements.txt

python3 desktop.py            # 桌面視窗版（三個功能都有 GUI）
python3 cli.py info           # 命令列版：檢查 OCR／數式引擎
python3 cli.py word  a.pdf b.pdf -o out/
python3 cli.py merge all.pdf a.pdf b.pdf
python3 cli.py split a3.pdf -o out/
```

耗資源的引擎（OCR／數式辨識）在**子程序**執行，GUI 本身保持輕量、不會因為模型載入而卡住；
轉檔過程中可以即時看到每頁進度與最後的統計（頁數、OCR 頁數、圖片數、方程式數）。

## 兩種轉檔引擎（都不需要外部伺服器）

| 引擎 | 何時使用 | OCR | 數式 | 速度 |
|---|---|---|---|---|
| **瀏覽器內建**（預設） | 直接開網頁，沒有跑 `server.py` 時自動啟用 | tesseract.js（WASM，內含 eng／繁中／簡中） | 保留為文字或圖片，**不會**變成 Word 方程式 | 較慢，一般頁面約 1–3 秒／頁 |
| **本專案 `server.py`** | 自己在電腦上執行 `python3 server.py`（同一網址）時自動改用 | RapidOCR | pix2tex → OMML，可轉成 Word 方程式 | 快，數式品質最好 |

兩者都用**本專案內的程式與程式庫**，沒有任何資料離開你的電腦；網頁不會呼叫 Render／Hugging Face 等外部主機。

線上版：`https://tyhcch1234.github.io/pdf-studio/`（GitHub Pages 只有靜態檔，所以在那裡用的是瀏覽器內建引擎）

### 本機啟動（可選，換取更好的數式與 OCR）

```bash
pip install -r requirements.txt
python3 server.py          # http://127.0.0.1:8000
```

页面會自動偵測同源的 `/api/capabilities`：偵測到就用 `server.py`，偵測不到就用瀏覽器引擎，不需要任何設定。

### 瀏覽器引擎的限制

- 數式不會變成 Word 方程式（瀏覽器跑不動 pix2tex），會保持為可編輯文字或圖片。
- 表格框線不會重建為表格，只保留可見線條所裁出的圖片（或被跳過以避免重複）。
- OCR 首次使用需載入約 6 MB 模型，之後快取。
- 大檔（>50 頁）建議改用 `server.py`。

## 網頁版（選用）

```bash
python3 server.py            # 預設 http://127.0.0.1:8000
PORT=9000 python3 server.py  # 換 port
```

網頁只是同一套 `pipeline/` 的另一個介面；不想用瀏覽器時可忽略。

第一次執行 PDF → Word 時，數式模型（pix2tex）會自動下載約 120 MB 權重到
`site-packages/pix2tex/model/checkpoints`，之後即可離線使用。

## 依賴與降級行為

| 元件 | 用途 | 缺少時 |
|---|---|---|
| PyMuPDF | 解析 PDF、取文字／圖片、算繪頁面 | 必需 |
| RapidOCR (onnxruntime) | 掃描頁 OCR（中英數混排） | 退回 PDF 內建文字層 |
| pix2tex (LaTeX-OCR) | 數式圖片 → LaTeX → OMML | 數式以高解析度圖片保留（仍可見、不可編輯） |
| python-docx | 產生 .docx | 必需 |
| latex2mathml + mathml2omml | LaTeX → OMML（Word 方程式） | 簡式公式改用內建轉換器 |

`GET /api/capabilities` 會回報目前可用的引擎，介面右上角會顯示狀態。

## 轉換流程

1. **逐頁解析**：取得文字行（含字體、大小、粗斜體、顏色）、內嵌圖片、向量圖形。
2. **OCR**：文字層少於 25 字元（或使用者選「一律 OCR」）時，以 200–300 dpi 算繪整頁交給
   RapidOCR；純掃描頁的全頁底圖會被排除，不會重複貼成一張大圖。
3. **數式**：以字體（CMMI/CMSY/Symbol/Cambria Math…）與運算符密度找出數式區域 →
   高解析度裁切 → pix2tex 轉 LaTeX → 轉 OMML。
   數位 PDF 若辨識結果與原字元差異過大，會**保留原本文字**而不是貼上錯誤公式。
4. **圖片／圖表**：內嵌圖片直接取用；若頁面有向量圖（框線、折線圖），比對「未被文字覆蓋的墨跡」
   區塊後另外裁切，避免圖表變成空白。
5. **閱讀順序**：XY-cut（先找欄與欄之間的空白，再找列之間的空白），支援單欄、雙欄與圖文混排。
6. **輸出**：段落依原始座標縮排、字級與樣式還原；頁面尺寸沿用原 PDF（可混合尺寸，會自動分段）。

## A3 → A4 切割邏輯

- 方向：橫向 A3 → 垂直切（左／右各一張 A4 直向）；直向 A3 → 水平切（上／下各一張 A4 橫向）。
- 旋轉頁面（`/Rotate` 90/180/270）會先正規化再切割，切完移除暫存頁。
- 「只切 A3 尺寸頁面」：非 A3 頁面原樣保留。
- 「校正為標準 A4 尺寸」：輸出對齊 595.28 × 841.89 pt（或橫向），差異在 40 pt 內才校正。

## API

```
GET  /api/capabilities
POST /api/convert        files[], ocr_mode=auto|always|never,
                         formula_mode=auto|off, keep_images, dpi
GET  /api/jobs/{id}      進度與結果
GET  /api/jobs/{id}/download/{file}
```

## 專案結構

```
server.py               FastAPI 服務、工作佇列、靜態檔
pipeline/
  extract.py            PDF → 版面元素（文字／圖片／數式）＋ OCR＋閱讀順序
  writer.py             版面元素 → docx（圖片、OMML 方程式）
  omml.py               LaTeX → OMML
  ocr.py                RapidOCR 封裝
  formula.py            pix2tex 封裝
  layout.py             XY-cut 與幾何工具
web/
  index.html            三個工具的介面
  css/style.css
  js/common.js          tabs、拖放、toast、下載
  js/convert.js         PDF → Word 前端
  js/merge.js           合併（pdf-lib）
  js/split.js           A3 → A4（pdf-lib + pdf.js 預覽）
tests/
  make_samples.py       產生測試樣本
  samples/              測試 PDF
```

## 已知限制

- 表格不會重建為 Word 表格，會以文字行 + 圖片方式保留（版面正確、非表格物件）。
- 加密 PDF 需先解除密碼。
- 數式辨識對低解析度掃描檔的準確率明顯下降；建議掃描 ≥ 300 dpi。
- 合併與切割用 pdf-lib，不處理加密檔案；表單欄位、書籤不會搬移。

## 驗證

```bash
python3 tests/make_samples.py     # 產生樣本 PDF（數位 / 掃描 / 中文雙欄 / A3）
python3 tests/ui_test.py          # 以真實瀏覽器跑完三個工具（需 playwright + chromium）
```

`ui_test.py` 會實際拖放檔案、按下按鈕、下載結果並檢查：
docx 內的文字與圖片、Word 方程式（含上標結構）、合併頁數、A3 切割後是否為兩張 A4 且左右內容正確。

## 已驗證的行為

| 樣本 | 結果 |
|---|---|
| 數位 PDF（含向量圖） | 文字 7 段、向量圖保留為圖片 |
| 掃描 PDF（純圖片） | 5 段 OCR 文字、發票金額轉為方程式 |
| 數學樣本（E=mc²、a²+b²=c²） | 2 條方程式，含上標結構（`m:sSup`） |
| 中文雙欄 + 表格 | 左欄 → 方程式 → 右欄 → 結論，表格線不重複貼圖 |
| A3 橫向 / 直向 / 旋轉 90° | 皆切成 2 張正確的 A4 |

## 安裝與疑難排解

```bash
pip install -r requirements.txt
python3 desktop.py --selftest     # 不開視窗，只檢查環境
python3 desktop.py                # 開桌面視窗
```

| 情況 | 處理 |
|---|---|
| Windows / macOS | 直接可用（tkinter 隨 Python 附帶） |
| Linux 開視窗報 `No module named tkinter` | `sudo apt install python3-tk`（Fedora：`python3-tkinter`） |
| 開視窗時出現 `xcb ... append_pending_request` 或直接關閉 | 某些 Linux 的 Tk 版本在建立複雜視窗時會中止。先跑 `--selftest` 把輸出貼回；或用 `python3 cli.py`、`python3 server.py`（同一套轉檔程式，只是介面不同） |
| OCR／數式顯示「未安裝」 | `pip install rapidocr-onnxruntime onnxruntime` 與 `pip install torch --index-url https://download.pytorch.org/whl/cpu` + `pip install pix2tex` |

`--selftest` 會檢查：Python 版本、PyMuPDF/python-docx/numpy/Pillow、OCR 與數式引擎、以及 tkinter 能否建立視窗（在子程序檢查，不會拖垮主程式）。
