"""
Notion API: 統合論文DBへのページ追加と重複チェック
既存IBD summary botと同じく requests 直叩き(notion-client は使わない)
"""
import requests
from typing import Dict, Optional, List

import config


NOTION_API = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {config.NOTION_TOKEN}",
    "Notion-Version": config.NOTION_VERSION,
    "Content-Type": "application/json",
}


def is_pmid_exists(pmid: str) -> bool:
    """指定PMIDの論文が既にDBに存在するかチェック"""
    payload = {
        "filter": {
            "property": "PMID",
            "rich_text": {"equals": pmid},
        },
        "page_size": 1,
    }

    resp = requests.post(
        f"{NOTION_API}/databases/{config.NOTION_DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"[Notion] Dedup check failed (status {resp.status_code}): {resp.text[:200]}")
        # 失敗時は安全側に倒して「存在する」扱いにし、重複投稿を防ぐ
        return True

    results = resp.json().get("results", [])
    return len(results) > 0


def create_paper_page(paper: Dict, summary: Dict) -> Optional[str]:
    """統合論文DBに新規ページを作成。成功時はpage URLを返す"""
    properties = _build_properties(paper, summary)
    children = _build_page_content(paper, summary)

    payload = {
        "parent": {"database_id": config.NOTION_DATABASE_ID},
        "properties": properties,
        "children": children,
    }

    resp = requests.post(f"{NOTION_API}/pages", headers=HEADERS, json=payload, timeout=30)

    if resp.status_code != 200:
        print(f"[Notion] Create page failed (status {resp.status_code}): {resp.text[:500]}")
        return None

    return resp.json().get("url")


def _build_properties(paper: Dict, summary: Dict) -> Dict:
    """Notion DBプロパティを構築"""
    from filter import map_pub_type_to_study_type, map_journal_to_select, infer_category

    props = {
        "Title": {
            "title": [{"text": {"content": paper["title"][:2000]}}],
        },
        "Title (JP)": {
            "rich_text": [{"text": {"content": summary.get("title_jp", "")[:2000]}}],
        },
        "PMID": {
            "rich_text": [{"text": {"content": paper["pmid"]}}],
        },
        "PubMed URL": {"url": paper["pubmed_url"]},
        "Authors": {
            "rich_text": [{"text": {"content": paper["authors"][:2000]}}],
        },
        "Source Bot": {"select": {"name": "IBD summary bot"}},
        "AI Summary": {
            "rich_text": [{"text": {"content": summary.get("ai_summary", "")[:2000]}}],
        },
        "Key Findings": {
            "rich_text": [{"text": {"content": summary.get("key_findings", "")[:2000]}}],
        },
        "Importance": {"select": {"name": summary.get("importance", "★")}},
        "Journal": {"select": {"name": map_journal_to_select(paper["journal_iso"])}},
        "Study Type": {"select": {"name": map_pub_type_to_study_type(paper["pub_types"])}},
        "Category": {
            "multi_select": [{"name": c} for c in infer_category(paper)],
        },
    }

    # 任意項目
    if paper.get("doi"):
        props["DOI"] = {"url": f"https://doi.org/{paper['doi']}"}

    if paper.get("estimated_if"):
        props["IF"] = {"number": paper["estimated_if"]}

    if paper.get("pub_date"):
        props["Pub Date"] = {"date": {"start": paper["pub_date"]}}

    # Relevance to Research(AIが判定したタグ)
    relevance_tags = summary.get("relevance_tags", [])
    valid_tags = {
        "ASUC risk stratification", "single-cell/spatial",
        "refractory UC subtyping", "biologic combo therapy",
        "ML/AI in IBD", "pharmacogenomics", "その他",
    }
    relevance_tags = [t for t in relevance_tags if t in valid_tags]
    if relevance_tags:
        props["Relevance to Research"] = {
            "multi_select": [{"name": t} for t in relevance_tags],
        }

    return props


def _build_page_content(paper: Dict, summary: Dict) -> List[Dict]:
    """ページ本文(子ブロック)を構築。Key Findingsを構造化表示"""
    blocks = []

    # ヘッダー: Importance + 判定理由
    importance = summary.get("importance", "★")
    reason = summary.get("importance_reason", "")
    blocks.append({
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"emoji": "⭐" if importance == "★★★" else "📌"},
            "rich_text": [{
                "type": "text",
                "text": {"content": f"Importance: {importance}\n{reason}"},
            }],
            "color": "yellow_background" if importance == "★★★" else "gray_background",
        },
    })

    # Key Findings(構造化要約)
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Key Findings"}}]},
    })
    key_findings = summary.get("key_findings", "")
    for paragraph in key_findings.split("\n"):
        if paragraph.strip():
            blocks.append(_paragraph_block(paragraph))

    # 解説
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "臨床医視点での解説"}}]},
    })
    blocks.append(_paragraph_block(summary.get("ai_summary", "")))

    # Abstract原文(折りたたみ)
    if paper.get("abstract"):
        blocks.append({
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": "Original Abstract"}}],
                "children": [
                    _paragraph_block(paper["abstract"][:2000])
                ],
            },
        })

    # 元論文へのリンク
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": "🔗 "}},
                {
                    "type": "text",
                    "text": {"content": "PubMed", "link": {"url": paper["pubmed_url"]}},
                },
            ],
        },
    })

    return blocks


def _paragraph_block(text: str) -> Dict:
    """テキストを段落ブロックに変換。Notionの2000文字制限に対応"""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}],
        },
    }
