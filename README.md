# meta-daily

[duellinksmeta.com](https://www.duellinksmeta.com)（DLM）の大会データを取得し、
**パッと見てわかる「大会入賞まとめ」** を Discord に通知するボットです。

集計対象は **大会の入賞構築（tournament placements）だけ**。ランクマ／KoG や featured の
デッキは除外します。個別デッキの細かいカードリストは DLM のサイトで見るのが一番なので、
ボットは **ダイジェスト（要約）** に徹し、各通知から DLM へのリンクを必ず張ります。

## まとめに載るもの

入賞構築から、**デッキタイプ** と **流行りの汎用札** の2本立てでまとめます。

1. **🏆 大会で勝っているデッキタイプ** — 直近の大会入賞構築を集計し、優勝回数・入賞数で
   ランキング（1位 = 🏆）。前の同期間との比較トレンド（📈📉🆕➖）付き。
2. **🃏 流行りの汎用札** — 複数の入賞構築・複数アーキタイプにまたがって採用されている
   汎用カード（Forbidden Droplet, Effect Veiler …）を採用率つきで。
3. **画像** — 大会ロゴ（APIの `tournamentType.icon`）と、注目汎用札のカード画像
   （DLM の S3 CDN）をサムネイルとして添付し、ひと目でわかるように。

トレンドは **ローリング期間（既定5日）** で計算し、その直前の同じ長さの期間と比較して
`📈上昇 / 📉下降 / 🆕新顔 / ➖横ばい` を判定します。入賞構築はサンプルが少ないため、
汎用札の判定しきい値（採用アーキタイプ数・1アーキタイプの最小デッキ数）は緩めています。

> サンプルの出力イメージは [`data/preview.json`](data/preview.json) を参照（合成データ）。
> 実データ版は push のたびに CI が自動生成します。

## 構成

```
src/dlm/
  client.py   — DLM API 取得（リトライ / バックオフ）
  models.py   — デッキ JSON を型付きオブジェクトに変換・分類
  analyze.py  — 採用率・枚数集計 + 汎用札の横断検出
  trends.py   — 入賞構築のみでトレンド算出（勝ちデッキ / 汎用札）
  assets.py   — カード画像・大会ロゴの画像URL生成
  render.py   — Discord 埋め込み（ダイジェスト）整形
  notify.py   — Webhook 送信（429 rate-limit 対応）
  bot.py      — digest / analyze / staples / preview コマンド
.github/workflows/
  digest.yml  — 1日1回（cron）＋ main への push（デプロイ）ごとに通知
  preview.yml — src 変更時に data/preview.json を自動生成（Discord 不要）
  probe.yml   — API 調査用（probe.py 変更時のみ）
scripts/
  selftest.py — 合成データでのオフライン検証（ネットワーク不要）
  probe.py    — DLM API のレスポンス形状調査
```

## コマンド

```bash
# まとめを組み立てて Discord に投稿（Webhook 未設定なら dry-run で内容を表示）
PYTHONPATH=src python -m dlm.bot digest

# 任意アーキタイプ（入賞構築）の確定枠 / 選択枠 / 汎用札を表示
PYTHONPATH=src python -m dlm.bot analyze "Sky Striker"

# 大会入賞構築の汎用札ランキングを表示
PYTHONPATH=src python -m dlm.bot staples

# 投稿せずに data/preview.json へ実データのダイジェストを書き出す
PYTHONPATH=src python -m dlm.bot preview
```

## 通知タイミング

- **1日1回**：`digest.yml` の cron（既定 09:00 UTC = 18:00 JST）。
- **デプロイ後すぐ**：`main` へ `src/**` を push すると即座に最新まとめを投稿。
- **手動**：Actions → DLM Meta Digest → Run workflow。

## 環境変数 / Secrets

| 名前 | 用途 | 既定 |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` (secret) | 投稿先 Webhook。未設定なら dry-run | — |
| `DISCORD_USERNAME` | Webhook 表示名 | `DLM Meta Digest` |
| `DLM_WINDOW_DAYS` (var) | 集計期間（日） | `5` |
| `DLM_MAX_PAGES` | コーパス取得の最大ページ数（×50件） | `20` |
| `DLM_PER_PAGE` | 1ページの件数 | `50` |

## マージ後にやること

1. Discord でチャンネルの Webhook URL を作成。
2. リポジトリの `Settings → Secrets and variables → Actions` に `DISCORD_WEBHOOK_URL` を登録。
3. Actions → DLM Meta Digest → Run workflow で手動実行して初回確認。

> Secret なしでも cron は動きますが dry-run（Discord 未投稿、ログ出力のみ）になります。
