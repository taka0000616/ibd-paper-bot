"""
Claude APIで論文の日本語要約とImportance判定を行う
"""
import json
import requests
from typing import Dict, Optional

import config


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


SUMMARIZE_PROMPT = """あなたは消化器内科専門医向けに最新論文を解説する医学エディタです。
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
  "key_findings": "**背景**: ...\\n**方法**: ...\\n**結果**: ...\\n**結論**: ...",
  "ai_summary": "臨床医視点での200-300字の解説。研究の意義、limitations、日常診療へのインパクトを含む",
  "importance": "★★★" or "★★" or "★",
  "importance_reason": "なぜそのImportanceを付けたか(1文)",
  "relevance_tags": ["ASUC risk stratification" | "single-cell/spatial" | "refractory UC subtyping" | "biologic combo therapy" | "ML/AI in IBD" | "pharmacogenomics" | "その他" のうち該当するもののリスト、無ければ空配列]
}}

# Importance判定基準
- ★★★: NEJM/Lancet/Nature級、または領域の診療を変えるpivotal trial、ガイドライン
- ★★: GI top tier (Gastroenterology/Gut)、大規模RCT、重要なMeta-analysis
- ★: Specialty journal、観察研究、研究デザインに限界あり

# 注意
- 数値・統計値(HR, OR, p値, CI, n数)は正確に引用すること
- 不明確な点を捏造しないこと、abstractに無い情報は推測で書かない
- 日本語の医学用語を使用(例: "ulcerative colitis"→"潰瘍性大腸炎")
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
