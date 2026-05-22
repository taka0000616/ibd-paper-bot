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
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_MAX_TOKENS = 2000

# ============================================================
# PubMed設定
# ============================================================
PUBMED_EMAIL = os.environ.get("PUBMED_EMAIL", "your-email@example.com")
PUBMED_API_KEY = os.environ.get("PUBMED_API_KEY", "")  # 任意。あれば3req/sec→10req/sec

# --- 検索範囲 ---
PUBMED_RELDATE_DAYS = 730  # 過去2年以内

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
    # GI top tier
    "Gastroenterology": 29.0,
    "Gut": 24.0,
    "Hepatology": 13.0,
    "J Hepatol": 26.0,
    # IBD/GI specialty
    "Am J Gastroenterol": 9.0,
    "Clin Gastroenterol Hepatol": 11.0,
    "J Crohns Colitis": 8.0,
    "Inflamm Bowel Dis": 5.0,
    "Aliment Pharmacol Ther": 6.0,
    "Lancet Gastroenterol Hepatol": 30.0,
    # Endoscopy
    "Endoscopy": 11.0,
    "Gastrointest Endosc": 7.0,
    # Oncology(irAE関連)
    "J Clin Oncol": 45.0,
    "Lancet Oncol": 51.0,
    "Ann Oncol": 50.0,
}

# IFスコア閾値: これ未満のジャーナルはスキップ
MIN_IF_THRESHOLD = 5.0

# Importance判定にAIを使うか
USE_AI_IMPORTANCE_JUDGMENT = True
# ★以下はNotion投稿をスキップ (False = ★も投稿、True = ★★以上のみ投稿)
SKIP_LOW_IMPORTANCE = False
