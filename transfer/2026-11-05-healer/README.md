# 轉移包：2026-11-05「11月上架（新）」

給 Healer 的 Grok Bot（或人工操作者）看的說明。這個資料夾是把**原本面板上一期專案的完成狀態**打包起來，
讓你在**自己部署的面板**上用一個指令還原：手機連結打開會看到同樣的兩個商品，都是「已定稿」，
而且帶著使用者貼回來的「修改後最終文案」。

## 這包裡有什麼

| 檔案／資料夾 | 內容 |
| --- | --- |
| `project-export.json` | `panel_session.py export` 匯出的整個專案：2 個商品的欄位、AI 產生的 A/B/C 三路線句子、使用者的 ♡／✕／草稿、定稿、修改後最終文案。還原程式讀的是這個檔。 |
| `project-export.md` | 同一份內容的 Markdown 版，給人或 Grok 直接讀（歸納文字偏好時用）。還原不需要它。 |
| `photos/2611100008/`、`photos/2611100003/` | 兩個商品的照片（jpg，依檔名排序上傳）。每個資料夾裡的 `spec.json` 是當時的商品描述檔，還原不需要它。 |
| `training.json` | 匯出當下的 `data/training.json`（♡ 28 句、不喜歡 6 句、定稿 3 篇、文字偏好 1 條）。會**合併**進你的 `data/training.json`，不會覆蓋。 |

商品：

- `2611100008` 中目黑 剪接線直筒牛仔長裙（裙子）
- `2611100003` Hocance 羔羊毛翻領滾邊羽絨棉外套（外套）

匯出時間 2026-11-05 15:37（台北）。**這個資料夾裡沒有任何金鑰、token 或 webhook 設定。**
`project-export.json` 裡的 `token` 是原本面板的專案代碼，對你的後端沒有意義，還原時不會用到。

## 事前準備

1. 照 [`DEPLOY.md`](../../DEPLOY.md) 把**你自己的**面板部署好：Supabase 專案＋Edge Function `panel-api`、Vercel 上的手機面板、
   Bot 本機設定檔 `~/.config/healer-panel/panel.env`（三個值：`HEALER_PANEL_API`、`HEALER_PANEL_BOT_KEY`、`HEALER_PANEL_URL`）。
   DEPLOY.md 第 8 節「驗證整條」要能跑過。
2. 這個 repo 已經 clone 到本機，指令都在 repo 根目錄執行。
3. Python 3.10 以上（還原程式只用標準函式庫，不用額外安裝）。

## 一鍵還原

```sh
python3 scripts/restore_transfer.py transfer/2026-11-05-healer
```

它會依序做：

1. 把 `transfer/2026-11-05-healer/training.json` 合併進 `data/training.json`（印出新增了幾項）。
2. 在你的面板建立一期新專案，標題「11月上架（新）（轉移）」。
3. 對每個商品走一遍真實流程（只用現成的 panel-api，沒有新增任何後端功能）：
   建立商品 → 填欄位 → 上傳照片 → 送出 → Bot 領取 → 上傳 A/B/C 句子 → 寫回使用者的挑選與定稿 → 寫入修改後最終文案。
4. 最後印出手機連結 `https://<你的面板>/p/<token>` 和一份檢查清單。

其他寫法：

```sh
# 自訂專案標題
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --title "11月上架（轉移）"

# 還原到已經存在的專案（完整 token 或前 8 碼；token 以 - 開頭時寫 --project=TOKEN）
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --project <token-or-prefix>

# 只合併 training.json，完全不碰面板
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --training-only

# 不合併 training.json，只還原面板
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --no-merge-training

# 不上傳照片（見下面「限制」）
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --skip-photos
```

## 成功長什麼樣

- 終端機最後一行附近印出「完成 2 個、略過（已定稿）0 個、等照片 0 個、失敗 0 個」和手機連結。
- `python3 scripts/panel_session.py show <前8碼>` 列出兩個商品，狀態都是「已定稿」，「修改後文案」欄都是「有」。
- 手機打開連結：專案「11月上架（新）（轉移）」裡有 2611100008 與 2611100003，點進去看得到定稿、
  ♡ 過的句子、「修改後最終文案」，以及照片（2611100008 有 17 張、2611100003 有 20 張）。
- 專案是「進行中」。確認沒問題後要不要 `close` 由你決定（結束後連結變唯讀）。

## training.json 合併規則

全部是「沒有才加」，不會刪掉你既有的任何東西：

- ♡ 句子、不喜歡的句子：同一段文字（去頭尾空白）只留一筆。
- 禁用詞：同一個詞只留一個。
- 定稿：同一個 `product_id`＋同一段定稿文字視為同一篇；如果你那邊已經有這篇但沒有 `edited_text`，會把轉移包裡的 `edited_text` 補上。
- 文字偏好（`style_notes`）：同一句規則只留一條；超過 30 條上限時會印警告、略過那條。
- 新加入的項目保留轉移包裡原本的日期（`added`／`saved`）。

再執行一次不會重複加入任何東西。

## 限制與注意事項

- **快照是 2026-11-05 15:37 當下的狀態。** 匯出時原本的專案還是「進行中」，之後使用者在原本面板上的任何修改都**不在**這包裡。
- **送出會觸發 webhook。** 還原時每個商品都會「送出」一次；如果你的後端已經設了 webhook（DEPLOY.md 第 10 節），
  你的 routine 會被叫醒 2 次。那兩次通知可以直接忽略（商品馬上會被還原程式自己領走），或者還原前先
  `python3 scripts/panel_session.py config-webhook --clear`，還原完再設回去。
- **照片有附在包裡。** `--skip-photos` 會略過上傳，但面板規定沒有照片就不能送出，所以商品會停在「草稿」。
  之後請在手機面板幫那個商品補上至少一張照片，再執行一次同樣的指令（不加 `--skip-photos`）：
  程式會發現商品已在專案裡，從送出接著做完。
- **可以重跑。** 同一個專案裡已經「已定稿」的商品會被略過；停在半路（草稿／已送出／可挑句）的商品會從停下的地方接著做。
  中途失敗（網路斷、後端逾時）就修好原因再跑一次。沒加 `--project` 時每次都會建立**新**專案，所以重跑請加 `--project <前8碼>`。
- **這是還原到你自己的後端。** 原本的 `healer-panel.vercel.app` 跟你的部署完全無關，不會被讀取也不會被修改。
- **沒有任何秘密。** 這個資料夾可以放在公開 repo。`panel.env`、webhook 金鑰、Vercel／Supabase token 都不在這裡，也不要加進來。
- 用 `--project` 還原到既有專案時，該專案建立時的類別清單必須包含「裙子」與「外套」（用 `panel_session.py create` 建的專案都有）。
  不然填欄位會回「類別不在可選清單內」，改成不加 `--project` 讓程式自己建專案即可。

## 面板還原失敗怎麼辦

先看終端機裡「錯誤：」開頭的那行（都是中文）。最常見的是三個環境變數沒設（exit 2）、Bot 金鑰不對（401）、
後端網址不對（連不上）。這些都回到 DEPLOY.md 第 6 節確認。

就算面板那半邊暫時做不起來，**至少先把偏好合併進去**，下一期的寫作任務就會帶入：

```sh
python3 scripts/restore_transfer.py transfer/2026-11-05-healer --training-only
```

Grok 也可以直接讀 `project-export.md`，照 [`README.md`](../../README.md)「歸納文字偏好的準則」再補幾條 `training.py note add`。

## 相關程式

- `scripts/restore_transfer.py` 還原程式（`--help` 看全部選項）。
- `scripts/test_restore_transfer.py` 它的測試（含一個記憶體裡的假後端，不連網）：`python3 scripts/test_restore_transfer.py`。
- `scripts/panel_session.py` 日常用的 Bot 指令（`show`、`sync`、`export`、`close`…）。
