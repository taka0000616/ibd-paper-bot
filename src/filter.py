"""
論文の信頼度スコアリングと事前フィルタ
PubMedで拾った論文 → ジャーナル/研究タイプで足切り → 高スコア順にソート
"""
from typing import List, Dict, Optional
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


def _recency_boost(pub_date: str) -> float:
    """
    新着論文への加点。pub_date は "YYYY-MM-DD" 形式
    過去30日以内: +20点
    過去90日以内: +10点
    過去180日以内: +5点
    それ以降: 0点
    """
    if not pub_date:
        return 0.0
    try:
        from datetime import datetime, timezone
        pub_dt = datetime.strptime(pub_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_ago = (datetime.now(timezone.utc) - pub_dt).days
        if days_ago <= 30:
            return 20.0
        elif days_ago <= 90:
            return 10.0
        elif days_ago <= 180:
            return 5.0
        else:
            return 0.0
    except (ValueError, TypeError):
        return 0.0


# 研究テーマ適合度の加点キーワード(正規表現, 小文字で判定)
# IFだけで並べると「高IF誌の総説」が「中堅誌のASUCレスキュー論文」より
# 上に来てしまうため、リサーチクエスチョンへの近さを明示的に加点する。
RELEVANCE_KEYWORDS = (
    (r"acute severe|asuc|steroid[- ]refractory|rescue therapy|colectomy", 12.0),
    (r"upadacitinib|tofacitinib|filgotinib|jak inhibitor|janus kinase", 8.0),
    (r"refractory|difficult[- ]to[- ]treat|treatment failure", 6.0),
    (r"dual (biologic|targeted)|combination (biologic|advanced)", 7.0),
    (r"single[- ]cell|spatial transcriptom|scrna", 8.0),
    (r"machine learning|deep learning|artificial intelligence|prediction model", 7.0),
    (r"pharmacogenom|nudt15|tpmt|hla-dqa1", 7.0),
    (r"risankizumab|mirikizumab|guselkumab|etrasimod|ozanimod|vedolizumab", 4.0),
    (r"biomarker|calprotectin|drug level|therapeutic drug monitoring", 4.0),
    (r"intestinal ultrasound|transmural healing|histologic remission", 4.0),
    (r"p(a)?ediatric|children", 3.0),
)


def relevance_boost(paper: Dict) -> float:
    """タイトル+abstractから研究テーマ適合度の加点を算出(上限あり)"""
    import re
    text = ((paper.get("title") or "") + " " + (paper.get("abstract") or "")).lower()
    if not text.strip():
        return 0.0
    score = 0.0
    for pattern, weight in RELEVANCE_KEYWORDS:
        if re.search(pattern, text):
            score += weight
    # IFを完全に塗り潰さないよう上限を設ける
    return min(score, 30.0)


def calculate_score(paper: Dict) -> float:
    """論文の総合スコア。

    Journal IF + Pub Type + Recency + 研究テーマ適合度 + 全文ボーナス の和。
    「重要度が高いもの・IFが高いものから優先」という方針に沿うよう、
    IF一辺倒ではなくリサーチクエスチョンへの近さも反映する。
    """
    # ジャーナルスコア(ホワイトリストになければ0)
    journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)

    # Pub Typeスコア(最大値を採用)
    pub_type_score = max(
        (PUB_TYPE_SCORE.get(pt, 0) for pt in paper["pub_types"]),
        default=0,
    )

    # 新着ボーナス
    recency = _recency_boost(paper.get("pub_date", ""))

    # 研究テーマ適合度
    relevance = relevance_boost(paper)

    # PMC全文が取得できる見込みの論文(= より厚い要約が作れる)を優先
    fulltext_bonus = config.FULLTEXT_SCORE_BONUS if paper.get("has_pmc") else 0.0

    # 重み付け: ジャーナルIFを主、Pub Typeを副、Recency/テーマ適合で優先度調整
    return journal_if + pub_type_score * 2 + recency + relevance + fulltext_bonus


def filter_and_rank(papers: List[Dict]) -> List[Dict]:
    """ジャーナルホワイトリストとIF閾値で足切りし、スコア降順で返す。

    config.FULLTEXT_RELAX_FILTER が True の場合、PMC全文が取得できる論文
    (has_pmc)は IF 閾値での足切りを免除する。ただし何でも通ると低品質誌の
    症例集積まで流入するため、エビデンスレベル(Publication Type)が
    一定以上であることを条件とする。
    config.FULLTEXT_ONLY が True の場合は全文ありの論文のみ残す。
    """
    filtered = []
    relaxed_in = 0
    for paper in papers:
        journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)
        has_pmc = bool(paper.get("has_pmc"))
        pub_type_score = max(
            (PUB_TYPE_SCORE.get(pt, 0) for pt in paper["pub_types"]),
            default=0,
        )

        passes_if = journal_if >= config.MIN_IF_THRESHOLD
        # 全文が読める & 研究デザインが観察研究以上なら、ホワイトリスト外でも通す
        passes_fulltext = (
            config.FULLTEXT_RELAX_FILTER
            and has_pmc
            and pub_type_score >= config.FULLTEXT_RELAX_MIN_PUB_TYPE_SCORE
        )

        if not (passes_if or passes_fulltext):
            continue
        if config.FULLTEXT_ONLY and not has_pmc:
            continue
        if passes_fulltext and not passes_if:
            relaxed_in += 1

        paper["score"] = calculate_score(paper)
        paper["estimated_if"] = journal_if
        filtered.append(paper)

    # スコア降順 → 同点なら新しい順
    filtered.sort(
        key=lambda p: (p["score"], p["pub_date"] or ""),
        reverse=True,
    )

    print(f"[Filter] {len(papers)} papers → {len(filtered)} after journal/type filter"
          + (f" (incl. {relaxed_in} admitted via PMC full text)" if relaxed_in else ""))

    return filtered


def map_pub_type_to_study_type(pub_types: List[str]) -> str:
    """PubMedのPublication TypeをNotionのStudy Type selectにマッピング

    注意: PubMedのPublication Typeは研究デザインを正確には表さない。
    特に "Clinical Trial" / "Clinical Trial, Phase II|III" は単群試験でも
    付与されるため、これをRCT扱いすると単群試験や症例集積が誤って
    "RCT" になる(実際に単群試験が複数RCTと誤表示されていた)。
    ランダム化の明示があるときのみRCTとする。
    AI要約が study_type を返す場合は resolve_study_type() 側でそれを優先する。
    """
    pt_set = set(pub_types)

    # 優先度順
    if "Meta-Analysis" in pt_set:
        return "Meta-analysis"
    if "Systematic Review" in pt_set:
        return "Systematic Review"
    if "Practice Guideline" in pt_set or "Guideline" in pt_set:
        return "Guideline"
    # ランダム化が明示されている場合のみRCT
    if "Randomized Controlled Trial" in pt_set or "Controlled Clinical Trial" in pt_set:
        return "RCT"
    if "Case Reports" in pt_set:
        return "Case Series"
    if "Review" in pt_set:
        return "Review"
    # 観察研究系。Comparative Studyは多くがコホート間比較であり
    # Case-controlに固定するのは不正確なためCohortに寄せる
    if any(k in pt_set for k in
           ("Observational Study", "Multicenter Study", "Validation Study",
            "Comparative Study")):
        return "Cohort"
    # 単群/非ランダム化の臨床試験はデザイン不明としてOther
    # (AIがstudy_typeを返せばそちらで上書きされる)
    if any("Clinical Trial" in pt for pt in pt_set):
        return "Other"
    return "Other"


# Notion の Study Type select に存在する値(ここに無い値は採用しない)
STUDY_TYPE_OPTIONS = {
    "RCT", "Meta-analysis", "Systematic Review", "Cohort", "Case-control",
    "Cross-sectional", "Case Series", "Basic Research", "Translational",
    "Review", "Guideline", "Other",
}


def resolve_study_type(paper: Dict, summary: Optional[Dict] = None) -> str:
    """Study Type を決定する。

    AI要約は本文/abstractを読んでデザインを判断できるため、
    有効な値を返していればそれを採用する。返していない/無効な値の場合は
    Publication Type ベースのマッピングにフォールバックする。
    """
    if summary:
        ai_type = (summary.get("study_type") or "").strip()
        if ai_type in STUDY_TYPE_OPTIONS:
            return ai_type
    return map_pub_type_to_study_type(paper.get("pub_types", []))


# Notion の Journal select に存在する値(= 唯一の正)。
# ここに無い名前を渡すとNotion側でvalidation errorになるため、
# マッピングの値は必ずこの集合に含まれること(下のassertで検証)。
NOTION_JOURNAL_OPTIONS = {
    "NEJM", "Lancet", "JAMA", "BMJ", "Nature", "Cell", "Nature Medicine",
    "Lancet Gastroenterol Hepatol", "Gastroenterology", "Gut", "Hepatology",
    "J Hepatol", "Liver Int", "Am J Gastroenterol", "CGH", "JCC",
    "IBD Journal", "Aliment Pharmacol Ther", "Endoscopy", "GIE",
    "Dig Endosc", "Endosc Int Open", "Pancreatology", "Pancreas",
    "Lancet Oncol", "J Clin Oncol", "Ann Oncol", "JAMA Oncol",
    "Gastric Cancer", "Ann Surg", "Ann Surg Oncol", "Clin Infect Dis",
    "Lancet Infect Dis", "J Infect Dis", "Hepatol Int", "Dig Liver Dis",
    "Dig Dis Sci", "World J Gastroenterol", "Am J Transplant",
    "Sci Transl Med", "Other",
}

# PubMedのISOAbbreviation → Notion Journal select
# 以前は17誌しか登録されておらず、Notion側に選択肢がある誌
# (Lancet Gastroenterol Hepatol, BMJ, Dig Liver Dis など)まで
# "Other" に落ちていた。
_JOURNAL_SELECT_MAP = {
    # General top tier
    "N Engl J Med": "NEJM",
    "Lancet": "Lancet",
    "JAMA": "JAMA",
    "BMJ": "BMJ",
    "Nature": "Nature",
    "Cell": "Cell",
    "Nat Med": "Nature Medicine",
    "Sci Transl Med": "Sci Transl Med",
    # GI / hepatology
    "Lancet Gastroenterol Hepatol": "Lancet Gastroenterol Hepatol",
    "Gastroenterology": "Gastroenterology",
    "Gut": "Gut",
    "Hepatology": "Hepatology",
    "J Hepatol": "J Hepatol",
    "Liver Int": "Liver Int",
    "Hepatol Int": "Hepatol Int",
    "Am J Gastroenterol": "Am J Gastroenterol",
    "Clin Gastroenterol Hepatol": "CGH",
    "J Crohns Colitis": "JCC",
    "Inflamm Bowel Dis": "IBD Journal",
    "Aliment Pharmacol Ther": "Aliment Pharmacol Ther",
    "Dig Liver Dis": "Dig Liver Dis",
    "Dig Dis Sci": "Dig Dis Sci",
    "World J Gastroenterol": "World J Gastroenterol",
    # Endoscopy
    "Endoscopy": "Endoscopy",
    "Gastrointest Endosc": "GIE",
    "Dig Endosc": "Dig Endosc",
    "Endosc Int Open": "Endosc Int Open",
    # Pancreas
    "Pancreatology": "Pancreatology",
    "Pancreas": "Pancreas",
    # Oncology (irAE関連)
    "Lancet Oncol": "Lancet Oncol",
    "J Clin Oncol": "J Clin Oncol",
    "Ann Oncol": "Ann Oncol",
    "JAMA Oncol": "JAMA Oncol",
    "Gastric Cancer": "Gastric Cancer",
    # Surgery
    "Ann Surg": "Ann Surg",
    "Ann Surg Oncol": "Ann Surg Oncol",
    # Infectious disease
    "Clin Infect Dis": "Clin Infect Dis",
    "Lancet Infect Dis": "Lancet Infect Dis",
    "J Infect Dis": "J Infect Dis",
    # Transplant
    "Am J Transplant": "Am J Transplant",
}

# マッピング値の打ち間違いを起動時に検出する
assert set(_JOURNAL_SELECT_MAP.values()) <= NOTION_JOURNAL_OPTIONS, (
    "map_journal_to_select has values not present in Notion Journal options: "
    f"{set(_JOURNAL_SELECT_MAP.values()) - NOTION_JOURNAL_OPTIONS}"
)


def map_journal_to_select(journal_iso: str) -> str:
    """ジャーナルISO略称をNotionのJournal selectにマッピング

    Notion側に対応する選択肢が無い誌(Immunity, Intest Res など)は "Other"。
    """
    return _JOURNAL_SELECT_MAP.get(journal_iso, "Other")


def infer_category(paper: Dict) -> List[str]:
    """論文タイトル/アブストラクトからCategoryを推測

    Notion の Category multi_select 選択肢:
      UC / Crohn / IBD-general / 肝臓 / 膵胆道 / 消化管 / 内視鏡 / 腫瘍 / 感染症 / Other
    """
    import re
    text = (paper.get("title") or "") + " " + (paper.get("abstract") or "")
    text = text.lower()
    categories: List[str] = []

    def has(*patterns: str) -> bool:
        return any(re.search(p, text) for p in patterns)

    # --- IBD本体 ---
    has_uc = has(r"ulcerative colitis", r"\buc\b")
    has_cd = has(r"crohn")
    if has_uc and not has_cd:
        categories.append("UC")
    elif has_cd and not has_uc:
        categories.append("Crohn")
    elif has_uc and has_cd:
        categories.append("IBD-general")
    elif has(r"inflammatory bowel disease", r"\bibd\b"):
        categories.append("IBD-general")

    # --- 関連領域(単語境界付きで誤マッチを防ぐ) ---
    if has(r"primary sclerosing cholangitis", r"\bpsc\b", r"hepatitis",
           r"liver (injury|disease|fibrosis)", r"cirrhosis", r"hepatotoxicity"):
        categories.append("肝臓")
    if has(r"pancreatitis", r"\bpancrea", r"cholangiocarcinoma",
           r"biliary", r"gallbladder", r"\bercp\b"):
        categories.append("膵胆道")
    if has(r"colorectal (cancer|neoplas|dysplasia)", r"\bcrc\b", r"dysplasia",
           r"checkpoint inhibitor", r"\birae\b", r"immune-related adverse",
           r"lymphoma", r"malignanc"):
        categories.append("腫瘍")
    if has(r"colonoscop", r"endoscop", r"\bmayo endoscopic", r"\bues\b",
           r"chromoendoscopy", r"balloon dilat"):
        categories.append("内視鏡")
    if has(r"clostridi", r"\bc\.? ?difficile", r"cytomegalovirus", r"\bcmv\b",
           r"tuberculosis", r"herpes zoster", r"opportunistic infection",
           r"sars-cov-2", r"covid-19", r"vaccin"):
        categories.append("感染症")
    if has(r"celiac", r"eosinophilic esophagitis", r"irritable bowel",
           r"short bowel", r"microscopic colitis", r"diverticul"):
        categories.append("消化管")

    # 重複排除しつつ順序維持
    seen = set()
    categories = [c for c in categories if not (c in seen or seen.add(c))]

    return categories if categories else ["IBD-general"]
