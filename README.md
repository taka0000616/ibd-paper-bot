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

GitHub Actions cron で JST 7:00 / 12:00 / 18:00 / 22:00 の1日4回。

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
