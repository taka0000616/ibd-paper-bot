"""
論文の信頼度スコアリングと事前フィルタ
PubMedで拾った論文 → ジャーナル/研究タイプで足切り → 高スコア順にソート
"""
from typing import List, Dict
import config


# Publication Typeのエビデンスレベルスコア
# 大きいほど高エビデンス
PUB_TYPE_SCORE = {
    "Meta-Analysis": 10,
    "Systematic Review": 9,
    "Practice Guideline": 9,
    "Guideline": 8,
    "Randomized Controlled Trial": 8,
    "Clinical Trial, Phase III": 7,
    "Clinical Trial, Phase II": 5,
    "Multicenter Study": 5,
    "Validation Study": 5,
    "Clinical Trial": 4,
    "Observational Study": 3,
    "Comparative Study": 2,
}


def calculate_score(paper: Dict) -> float:
    """論文の総合スコア。Journal IF + Pub Type score の重み付き和"""
    # ジャーナルスコア(ホワイトリストになければ0)
    journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)

    # Pub Typeスコア(最大値を採用)
    pub_type_score = max(
        (PUB_TYPE_SCORE.get(pt, 0) for pt in paper["pub_types"]),
        default=0,
    )

    # 重み付け: ジャーナルIFを主、Pub Typeを副
    return journal_if + pub_type_score * 2


def filter_and_rank(papers: List[Dict]) -> List[Dict]:
    """
    ジャーナルホワイトリストとIF閾値で足切り
    残った論文をスコア降順にソートしてMAX件数だけ返す
    """
    filtered = []
    for paper in papers:
        journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)

        # ホワイトリスト外 or IF閾値未満は除外
        if journal_if < config.MIN_IF_THRESHOLD:
            continue

        paper["score"] = calculate_score(paper)
        paper["estimated_if"] = journal_if
        filtered.append(paper)

    # スコア降順 → 同点なら新しい順
    filtered.sort(
        key=lambda p: (p["score"], p["pub_date"] or ""),
        reverse=True,
    )

    print(f"[Filter] {len(papers)} papers → {len(filtered)} after journal/type filter")

    return filtered[:config.PAPERS_PER_RUN_MAX]


def map_pub_type_to_study_type(pub_types: List[str]) -> str:
    """PubMedのPublication TypeをNotionのStudy Type selectにマッピング"""
    pt_set = set(pub_types)

    # 優先度順
    if "Meta-Analysis" in pt_set:
        return "Meta-analysis"
    if "Systematic Review" in pt_set:
        return "Systematic Review"
    if "Practice Guideline" in pt_set or "Guideline" in pt_set:
        return "Guideline"
    if "Randomized Controlled Trial" in pt_set:
        return "RCT"
    if any("Clinical Trial" in pt for pt in pt_set):
        return "RCT"  # Clinical TrialはRCTに準じる扱い
    if "Validation Study" in pt_set:
        return "Cohort"
    if "Multicenter Study" in pt_set or "Observational Study" in pt_set:
        return "Cohort"
    if "Comparative Study" in pt_set:
        return "Case-control"
    if "Review" in pt_set:
        return "Review"
    return "Other"


def map_journal_to_select(journal_iso: str) -> str:
    """ジャーナルISO略称をNotionのJournal selectにマッピング"""
    # NotionのJournal selectで定義済みのもの
    mapping = {
        "N Engl J Med": "NEJM",
        "Lancet": "Lancet",
        "JAMA": "JAMA",
        "Nature": "Nature",
        "Cell": "Cell",
        "Nat Med": "Nature Medicine",
        "Gastroenterology": "Gastroenterology",
        "Gut": "Gut",
        "Hepatology": "Hepatology",
        "J Hepatol": "J Hepatol",
        "Am J Gastroenterol": "Am J Gastroenterol",
        "Clin Gastroenterol Hepatol": "CGH",
        "J Crohns Colitis": "JCC",
        "Inflamm Bowel Dis": "IBD Journal",
        "Aliment Pharmacol Ther": "Aliment Pharmacol Ther",
        "Endoscopy": "Endoscopy",
        "Gastrointest Endosc": "GIE",
    }
    return mapping.get(journal_iso, "Other")


def infer_category(paper: Dict) -> List[str]:
    """論文タイトル/アブストラクトからCategoryを推測"""
    text = (paper["title"] + " " + paper["abstract"]).lower()
    categories = []

    # IBD
    has_uc = any(k in text for k in ["ulcerative colitis", "uc patient"])
    has_cd = any(k in text for k in ["crohn", "crohn's"])
    if has_uc and not has_cd:
        categories.append("UC")
    elif has_cd and not has_uc:
        categories.append("Crohn")
    elif has_uc and has_cd:
        categories.append("IBD-general")
    elif "inflammatory bowel disease" in text or "ibd" in text:
        categories.append("IBD-general")

    # 関連領域
    if "primary sclerosing cholangitis" in text or "psc" in text:
        categories.append("肝臓")
    if "checkpoint inhibitor" in text or "irae" in text:
        categories.append("腫瘍")

    return categories if categories else ["IBD-general"]
