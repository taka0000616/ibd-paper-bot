# IBD Paper Bot

PubMedから過去6ヶ月以内のIBD関連高品質論文を取得し、Claude APIで日本語要約してNotion統合DBに自動投稿するbot。

## スコープ

- IBD (UC, Crohn's disease)
- PSC-IBD
- Immune checkpoint inhibitor関連の colitis / cholangitis / enterocolitis
- irAE (gastrointestinal)

## 信頼度フィルタ(3層)

1. **PubMed検索段階**: Publication Type を RCT / Meta-analysis / Systematic Review / Multicenter Study / Clinical Trial / Validation Study / Guideline などに限定。Case Report, Letter, Editorial は除外
2. **ジャーナルホワイトリスト**: 推定IF ≥ 5.0 のジャーナルのみ採用
3. **AI判定**: Claudeが研究デザインとサンプルサイズから★★★/★★/★を自動判定

## 実行スケジュール

GitHub Actions cron で JST 7:00 の1日1回。

## API コストの扱い

要約は **Message Batches API** でまとめて実行する(入力・出力トークンの単価が
一律 50%)。1日1回の cron なので結果の即時性は不要という前提に立っている。

- バッチが使えない環境では自動的に同期呼び出しへ退避する(割引は効かない)
- バッチは投入から完了まで時間がかかるため、実行時間内(既定40分)に
  終わらない場合はバッチIDを `cache/` に保存し、**次回の実行の冒頭で回収**する。
  これをしないと「課金済みなのにNotionへ書かれない」取りこぼしが起きる
- ランナーは実行ごとに破棄されるので、`cache/` は `actions/cache` で持ち越す
  (`cache/summaries/` = 要約キャッシュ、`cache/summaries/pending_batch.json` = 未完了バッチID)
- 要約キャッシュがあるので、Notion書き込みに失敗した論文を翌日
  もう一度APIに投げて二重課金することがない
- 重複排除は PMID と DOI の両方で行う(ahead-of-print と確定版は PMID が別)
- abstract が実質無く PMC 全文も無い文献(E-Videos・research letter 等)は
  要約不能なので API に投げる前に除外する

出力長(7節構成の厚み)は意図的に削っていない。品質がこのbotの目的なので、
数セントのために要約を薄くするより、バッチ割引で単価を下げる方針を採る。

## セットアップ

### 1. リポジトリ作成

```bash
git init ibd-paper-bot
cd ibd-paper-bot
# このコードベース一式をコピー
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:<your-account>/ibd-paper-bot.git
git push -u origin main
```

### 2. GitHub Secrets設定

リポジトリの Settings → Secrets and variables → Actions で以下を設定:

| Secret名 | 値 |
|---|---|
| `NOTION_TOKEN` | Notion Integration token (`secret_...`) |
| `NOTION_DATABASE_ID` |  (統合論文DBのDatabase ID) |
| `ANTHROPIC_API_KEY` | Anthropic API key (`sk-ant-...`) |
| `PUBMED_EMAIL` | NCBIに登録したメールアドレス |
| `PUBMED_API_KEY` | (任意) NCBI API key |

### 3. Notion Integration設定

- https://www.notion.so/profile/integrations から Internal Integration を作成
- 統合論文DBページに Integration を接続(右上 ... → Connections)

### 4. ローカルテスト実行

```bash
cp .env.example .env
# .envに各種シークレットを記入
pip install -r requirements.txt
export $(cat .env | xargs)
python src/main.py
```

## チューニング

`src/config.py` で以下を調整可能:

- `PUBMED_RELDATE_DAYS`: 検索範囲(デフォルト180日)
- `PAPERS_PER_RUN_MAX`: 1回の処理上限(デフォルト20件)
- `MIN_IF_THRESHOLD`: IFカットオフ(デフォルト5.0)
- `JOURNAL_WHITELIST`: 採用ジャーナル一覧
- `SKIP_LOW_IMPORTANCE`: True にすると★評価の論文は投稿しない

## ファイル構成

```
src/
├── main.py          # エントリーポイント
├── config.py        # 設定値
├── pubmed.py        # PubMed E-utilities ラッパー
├── filter.py        # スコアリングとフィルタ
├── ai_summarize.py  # Claude API要約
└── notion_writer.py # Notion API投稿
```
