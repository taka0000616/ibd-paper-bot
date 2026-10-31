"""
IBD Paper Bot - 設定
全パラメータをここに集約。動作変更したい時はこのファイルだけ編集する。
"""
import os

# ============================================================
# Notion設定
# ============================================================
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
# 統合論文DBの Database ID: a65693b6-4d0d-4050-8713-8300ee4a7fba
NOTION_VERSION = "2022-06-28"

# ============================================================
# Claude API設定
# ============================================================
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 6000
# 全文ベースで要約する場合はより長い出力を許可
CLAUDE_MAX_TOKENS_FULLTEXT = 12000

# ============================================================
# PubMed設定
# ============================================================
PUBMED_EMAIL = os.environ.get("PUBMED_EMAIL", "your-email@example.com")
PUBMED_API_KEY = os.environ.get("PUBMED_API_KEY", "")  # 任意。あれば3req/sec→10req/sec

# --- 検索範囲 ---
PUBMED_RELDATE_DAYS = 1825  # 過去5年以内

# esearchの取得上限。sort=pub_date(新しい順)のため、この値が小さいと
# 期間を広げても「最新N件」しか拾えない(古い論文が取得対象から漏れる)。
# 5年分(数千件)を確実に渉猟するため大きめに設定。NCBIのesearch上限は10000。
PUBMED_RETMAX = 5000

# --- 検索クエリ: IBD + 関連領域 ---
# Publication Type フィルタで信頼度の高い研究デザインに限定
PUBMED_QUERY = """(
  "inflammatory bowel disease"[MeSH] OR "ulcerative colitis"[MeSH] OR "Crohn disease"[MeSH]
  OR "IBD"[tiab] OR "ulcerative colitis"[tiab] OR "Crohn's disease"[tiab] OR "Crohn disease"[tiab]
  OR "primary sclerosing cholangitis"[MeSH] OR "PSC-IBD"[tiab]
  OR (("immune checkpoint inhibitor"[tiab] OR "checkpoint inhibitor"[tiab] OR "ICI"[tiab] OR "anti-PD-1"[tiab] OR "anti-CTLA-4"[tiab])
      AND ("colitis"[tiab] OR "cholangitis"[tiab] OR "enterocolitis"[tiab] OR "hepatitis"[tiab]))
  OR "checkpoint inhibitor colitis"[tiab]
  OR ("irAE"[tiab] AND ("colitis"[tiab] OR "cholangitis"[tiab]))
)
AND (
  Randomized Controlled Trial[ptyp]
  OR Meta-Analysis[ptyp]
  OR Systematic Review[ptyp]
  OR Multicenter Study[ptyp]
  OR Clinical Trial, Phase III[ptyp]
  OR Clinical Trial, Phase II[ptyp]
  OR Clinical Trial[ptyp]
  OR Observational Study[ptyp]
  OR Comparative Study[ptyp]
  OR Validation Study[ptyp]
  OR Practice Guideline[ptyp]
  OR Guideline[ptyp]
)
AND English[lang]
NOT (Editorial[ptyp] OR Letter[ptyp] OR Comment[ptyp] OR News[ptyp] OR Case Reports[ptyp])"""

# 1回の実行で処理する最大件数
PAPERS_PER_RUN_MAX = 10

# ============================================================
# ジャーナルホワイトリスト: 信頼度の高いジャーナル
# ============================================================
# キーは ISO略称(PubMedのISOAbbreviation)、値はおおよそのIF
# IFスコアリングと「Top tier優先」判定に使用
JOURNAL_WHITELIST = {
    # Top tier general
    "N Engl J Med": 96.0,
    "Lancet": 98.0,
    "JAMA": 63.0,
    "BMJ": 105.0,
    # Top basic/translational
    "Nature": 50.0,
    "Cell": 45.0,
    "Nat Med": 58.0,
    "Sci Transl Med": 17.0,
    "Cell Host Microbe": 30.0,
    "Immunity": 25.0,
    "Nat Immunol": 27.0,
    "Sci Immunol": 18.0,
    "Mucosal Immunol": 8.0,
    # GI top tier
    "Gastroenterology": 29.0,
    "Gut": 24.0,
    "Hepatology": 13.0,
    "J Hepatol": 26.0,
    "Lancet Gastroenterol Hepatol": 30.0,
    # IBD/GI specialty
    "Am J Gastroenterol": 9.0,
    "Clin Gastroenterol Hepatol": 11.0,
    "J Crohns Colitis": 8.0,
    "Inflamm Bowel Dis": 5.0,
    "Aliment Pharmacol Ther": 6.0,
    # 中堅GI誌(中堅IBD/消化器)
    "United European Gastroenterol J": 5.0,
    "Therap Adv Gastroenterol": 4.0,
    "Dig Dis Sci": 3.5,
    "J Gastroenterol": 6.0,
    "J Gastroenterol Hepatol": 4.0,
    "Dig Liver Dis": 4.0,
    "Scand J Gastroenterol": 3.0,
    "Eur J Gastroenterol Hepatol": 3.0,
    "World J Gastroenterol": 4.0,
    "BMC Gastroenterol": 3.0,
    "Intest Res": 3.5,
    "Crohns Colitis 360": 3.0,
    # Endoscopy
    "Endoscopy": 11.0,
    "Gastrointest Endosc": 7.0,
    "Dig Endosc": 6.0,
    "Endosc Int Open": 3.0,
    # Oncology(irAE関連)
    "J Clin Oncol": 45.0,
    "Lancet Oncol": 51.0,
    "Ann Oncol": 50.0,
}

# IFスコア閾値: これ未満のジャーナルはスキップ
MIN_IF_THRESHOLD = 3.0

# ============================================================
# 全文(PMC フルテキスト)設定
# ============================================================
# PMC無料全文(Open Accessサブセット)が取得できる論文を優先し、
# 全文があれば全文ベースで詳細要約、無ければabstractで要約する。

# 全文が取得できた論文は、ジャーナルホワイトリスト/IF閾値の足切りを免除する
# (= 全文要件でフィルタを緩和し、網羅性を上げる)
FULLTEXT_RELAX_FILTER = True

# 全文で足切りを免除する際の最低エビデンスレベル(filter.PUB_TYPE_SCORE基準)。
# 3 = Observational Study 以上。0にすると低品質誌の論文まで流入する。
FULLTEXT_RELAX_MIN_PUB_TYPE_SCORE = 3

# 全文があるものだけを投稿対象にする(True)か、abstractのみの論文も許容する(False)
# 「全文優先・abstract補完」方針 → False
FULLTEXT_ONLY = False

# 並べ替え時、全文ありの論文に加算するスコアボーナス
FULLTEXT_SCORE_BONUS = 8.0

# Claudeに渡す全文の最大文字数。
# 実測(除去後の本文7本)では 16.9k〜27.0k文字・中央値 23.6k だったので、
# 60000 はほぼ発動しない上限だった = コスト削減効果はほとんど無い。
# ここを中央値より下に下げると大半の論文で Discussion が削られ、
# 「問題点・批判的吟味」の材料が失われるため、品質を落とさない範囲の
# セーフティネットとして 30000 に置く(長大な総説・多施設報告のみ発動)。
# 発動時は先頭打ち切りではなくセクション重要度順に詰める
# (pubmed._trim_sections を参照)。
FULLTEXT_MAX_CHARS = 30000

# 要約キャッシュ(API二重課金の防止)
# Notion書き込みが失敗した論文は翌回の実行で再度APIに投げていたため、
# 同じ論文の要約に二度課金していた。要約結果をここに保存して再利用する。
SUMMARY_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "summaries")
USE_SUMMARY_CACHE = True

# Message Batches API を使うか。
# 1日1回の cron で結果の即時性が不要なので、入出力トークンが一律50%になる
# バッチを既定にする。使えない環境では自動的に同期呼び出しへ退避する。
USE_BATCH_API = True
# バッチの完了を待つ上限(秒)。超えたらバッチIDを保存して終了し、次回の実行で回収する
BATCH_MAX_WAIT_SEC = 2400      # 40分
BATCH_POLL_INTERVAL_SEC = 30
# ★止まりでスキップされる分を見込んで、目標より多めにバッチへ投入する倍率
BATCH_OVERSHOOT = 1.5

# abstractがこの長さ未満の文献はAPIに投げずにスキップする。
# E-Videos・research letter・学会抄録など、要約不能なものに課金していた。
MIN_ABSTRACT_CHARS = 300

# Importance判定にAIを使うか
USE_AI_IMPORTANCE_JUDGMENT = True
# ★以下はNotion投稿をスキップ (False = ★も投稿、True = ★★以上のみ投稿)
SKIP_LOW_IMPORTANCE = False
