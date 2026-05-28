"""
Claude APIで論文の日本語要約とImportance判定を行う
"""
import json
import requests
from typing import Dict, Optional

import config


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


SUMMARIZE_PROMPT = """あなたは消化器内科専門医(特にIBD臨床・研究)向けに最新論文を解説する医学エディタです。
以下の論文のabstractを読み、日本語で構造化された要約を作成してください。

# 論文情報
- タイトル: {title}
- ジャーナル: {journal} (推定IF: {if_score})
- 発行日: {pub_date}
- 研究タイプ: {pub_types}
- アブストラクト:
{abstract}

# 出力形式(必ずJSONのみ、コードブロックなし)
{{
  "title_jp": "日本語タイトル(簡潔に、医学用語は適切に)",
  "key_findings": "**背景・目的**(2-3文): 臨床的疑問と研究の位置づけ\\n\\n**方法**(2-3文): デザイン、対象集団のn数と特徴、主要評価項目、解析手法\\n\\n**結果**(3-5文): 主要評価項目の結果を具体的な数値(HR/OR/RR/CI/p値)とともに記載。重要な副次評価項目や安全性データも含む\\n\\n**結論**(2-3文): 著者の結論と、それを支持/制限するデータ上のポイント\\n\\n**臨床的解釈**(3-4文): 既存エビデンスとの位置づけ、研究のstrengths、limitations、日常診療や今後のIBD研究への含意",
  "ai_summary": "100字以内で論文の核心を一文で。「何の集団に、何を検証し、どんな結果だったか」を凝縮",
  "importance": "★★★" or "★★" or "★",
  "importance_reason": "なぜそのImportanceを付けたか(1文)",
  "relevance_tags": ["ASUC risk stratification" | "single-cell/spatial" | "refractory UC subtyping" | "biologic combo therapy" | "ML/AI in IBD" | "pharmacogenomics" | "その他" のうち該当するもののリスト、無ければ空配列]
}}

# Importance判定基準
- ★★★: NEJM/Lancet/Nature級、または領域の診療を変えるpivotal trial、ガイドライン
- ★★: GI top tier (Gastroenterology/Gut/Lancet GH等)、大規模RCT、重要なMeta-analysis
- ★: Specialty journal、観察研究、研究デザインに限界あり

# 注意
- 数値・統計値(HR, OR, p値, CI, n数)は正確に引用すること。Key Findingsの「結果」セクションでは特に丁寧に
- 不明確な点を捏造しないこと、abstractに無い情報は推測で書かない
- 日本語の医学用語を使用(例: "ulcerative colitis"→"潰瘍性大腸炎")
- ai_summary は厳密に100字以内。冗長な前置きや「本研究では」等の表現は省く
- key_findings は厚みを持たせる。特に「臨床的解釈」セクションでは、論文を読んだだけでは見えにくい既存研究との関係や、limitations の意義、IBDの病態理解や鷹将さん的なリサーチクエスチョン(ASUC risk, single-cell/spatial, biologic combo, ML/AI 等)との関連を意識して補足する
"""


def summarize_paper(paper: Dict) -> Optional[Dict]:
    """論文を要約してImportance判定する"""
    prompt = SUMMARIZE_PROMPT.format(
        title=paper["title"],
        journal=paper["journal_iso"],
        if_score=paper.get("estimated_if", "?"),
        pub_date=paper["pub_date"] or "unknown",
        pub_types=", ".join(paper["pub_types"]),
        abstract=paper["abstract"] or "(no abstract available)",
    )

    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": config.CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # レスポンスから本文を抽出
        content = data["content"][0]["text"].strip()

        # JSON部分を抜き出し(コードブロックが付いていれば剥がす)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        parsed = json.loads(content)
        return parsed

    except json.JSONDecodeError as e:
        print(f"[Claude] JSON parse error for PMID {paper['pmid']}: {e}")
        print(f"Raw content: {content[:500]}")
        return None
    except Exception as e:
        print(f"[Claude] API error for PMID {paper['pmid']}: {e}")
        return None
