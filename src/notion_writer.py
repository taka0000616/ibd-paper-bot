"""
Notion API: 統合論文DBへのページ追加と重複チェック
既存IBD summary botと同じく requests 直叩き(notion-client は使わない)
"""
import time
import requests
from typing import Dict, Optional, List, Set

import config


NOTION_API = "https://api.notion.com/v1"

# Notion APIの制約: rich_text要素1つあたり2000字、1プロパティ/ブロックあたり100要素
_RICH_TEXT_CHUNK = 2000
_RICH_TEXT_MAX_ITEMS = 100


def _rich_text(text: str) -> List[Dict]:
    """長文を2000字ごとのrich_text要素に分割する(切り捨てによる情報欠落を防ぐ)"""
    text = text or ""
    chunks = [text[i:i + _RICH_TEXT_CHUNK] for i in range(0, len(text), _RICH_TEXT_CHUNK)]
    return [{"type": "text", "text": {"content": c}} for c in chunks[:_RICH_TEXT_MAX_ITEMS]]

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


def get_existing_pmids() -> Optional[Set[str]]:
    """DB内の全PMIDを一括取得してセットで返す。

    これをループ前に1度だけ呼び、候補から既存論文を事前除外することで、
    「スコア上位の高IF論文(全て登録済み)を毎回100件走査して打ち切り」
    という停止問題を回避する。
    取得に失敗した場合は None を返し、呼び出し側で従来の逐次チェックに
    フォールバックする。
    """
    pmids: Set[str] = set()
    start_cursor: Optional[str] = None

    while True:
        payload: Dict = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = requests.post(
            f"{NOTION_API}/databases/{config.NOTION_DATABASE_ID}/query",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[Notion] get_existing_pmids failed (status {resp.status_code}): {resp.text[:200]}")
            return None

        data = resp.json()
        for page in data.get("results", []):
            rich = page.get("properties", {}).get("PMID", {}).get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich).strip()
            if text:
                pmids.add(text)

        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
        time.sleep(0.4)  # レート制限対策(3req/sec)

    print(f"[Notion] Loaded {len(pmids)} existing PMIDs from DB")
    return pmids


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
    from filter import resolve_study_type, map_journal_to_select, infer_category

    props = {
        "Title": {
            "title": _rich_text(paper["title"]),
        },
        "Title (JP)": {
            "rich_text": _rich_text(summary.get("title_jp", "")),
        },
        "PMID": {
            "rich_text": [{"text": {"content": paper["pmid"]}}],
        },
        "PubMed URL": {"url": paper["pubmed_url"]},
        "Authors": {
            "rich_text": _rich_text(paper["authors"]),
        },
        "Source Bot": {"select": {"name": "IBD summary bot"}},
        "AI Summary": {
            "rich_text": _rich_text(summary.get("ai_summary", "")),
        },
        "Key Findings": {
            "rich_text": _rich_text(summary.get("key_findings", "")),
        },
        "Importance": {"select": {"name": summary.get("importance", "★")}},
        "Journal": {"select": {"name": map_journal_to_select(paper["journal_iso"])}},
        # AI要約がデザインを判定していればそれを採用(Publication Typeは
        # 単群試験にも "Clinical Trial" を付けるため当てにならない)
        "Study Type": {"select": {"name": resolve_study_type(paper, summary)}},
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
    """ページ本文(子ブロック)を構築

    レイアウト:
    1. Importance callout (黄/灰のcallout)
    2. AI Summary (120字程度の結論サマリ、青のcallout)
    3. Key Findings (背景/方法/結果/結論/臨床的解釈 をセクション別に表示)
    4. Original Abstract (折りたたみ)
    5. PubMedリンク
    """
    blocks = []

    # 1. ヘッダー: Importance + 判定理由
    importance = summary.get("importance", "★")
    reason = summary.get("importance_reason", "")
    blocks.append({
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"emoji": "⭐" if importance == "★★★" else "📌"},
            "rich_text": _rich_text(f"Importance: {importance}\n{reason}"),
            "color": "yellow_background" if importance == "★★★" else "gray_background",
        },
    })

    # 2. AI Summary (120字程度の結論)
    ai_summary_text = summary.get("ai_summary", "")
    if ai_summary_text:
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"emoji": "💡"},
                "rich_text": _rich_text(ai_summary_text),
                "color": "blue_background",
            },
        })

    # 3. Key Findings (セクション別に展開)
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Key Findings"}}]},
    })
    key_findings = summary.get("key_findings", "")
    blocks.extend(_render_key_findings_sections(key_findings))

    # 4. Abstract原文(折りたたみ)
    if paper.get("abstract"):
        blocks.append({
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": "Original Abstract"}}],
                "children": [_paragraph_block(paper["abstract"])],
            },
        })

    # 元論文へのリンク(PMC全文があれば併記)
    link_rt: List[Dict] = [
        {"type": "text", "text": {"content": "🔗 "}},
        {"type": "text", "text": {"content": "PubMed", "link": {"url": paper["pubmed_url"]}}},
    ]
    if paper.get("pmc_url"):
        link_rt.append({"type": "text", "text": {"content": " ・ "}})
        link_rt.append({
            "type": "text",
            "text": {"content": "PMC全文", "link": {"url": paper["pmc_url"]}},
        })
    if paper.get("doi"):
        link_rt.append({"type": "text", "text": {"content": " ・ "}})
        link_rt.append({
            "type": "text",
            "text": {"content": "DOI", "link": {"url": f"https://doi.org/{paper['doi']}"}},
        })
    # 要約が全文ベースかabstractベースかを明示(要約の厚みの違いの根拠になる)
    src = summary.get("_source")
    if src:
        label = "全文ベース要約" if src == "fulltext" else "abstractベース要約"
        if src == "fulltext" and paper.get("full_text_truncated"):
            label += "(本文一部省略)"
        link_rt.append({"type": "text", "text": {"content": f"  —  {label}"}})
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": link_rt},
    })

    return blocks


def _paragraph_block(text: str) -> Dict:
    """テキストを段落ブロックに変換。2000字を超える場合はrich_text要素を分割して全文を保持"""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _render_key_findings_sections(key_findings_text: str) -> List[Dict]:
    """
    Key Findings テキストをセクション別に分解してNotionブロック化する

    入力例(改行で区切られたセクション):
        **背景・目的**: ...
        **方法**: ...
        **結果**: ...
        **結論**: ...
        **臨床的解釈**: ...

    各セクションを heading_3 + paragraph として展開する
    """
    import re

    blocks: List[Dict] = []

    # セクション見出しを抽出。
    # 行頭の **xxx**: / **xxx**：/ **xxx**(コロンなし) / ## xxx のいずれにも対応する。
    # 以前はコロン必須だったため、モデルがコロンを付けずに出力すると
    # セクション構造を検出できず、全文がフラットな段落として出ていた。
    section_pattern = re.compile(
        r"^[ \t]*(?:\*\*(?P<b>[^*\n]{1,40}?)\*\*|#{2,3}[ \t]+(?P<h>[^\n:：]{1,40}))"
        r"[ \t]*[:：]?[ \t]*",
        re.MULTILINE,
    )

    def _split_sections(text: str):
        """[preamble, heading, body, heading, body, ...] を返す"""
        matches = list(section_pattern.finditer(text))
        if not matches:
            return [text]
        out = [text[: matches[0].start()]]
        for i, m in enumerate(matches):
            heading = (m.group("b") or m.group("h") or "").strip()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            out.append(heading)
            out.append(text[m.end():end])
        return out

    parts = _split_sections(key_findings_text)

    if len(parts) <= 1:
        # セクション構造が見つからない場合: そのまま全文を段落として出力
        for paragraph in key_findings_text.split("\n"):
            if paragraph.strip():
                blocks.append(_paragraph_block(paragraph.strip()))
        return blocks

    # 最初の要素(前置き)が空でなければ段落として追加
    preamble = parts[0].strip()
    if preamble:
        blocks.append(_paragraph_block(preamble))

    # ペアで処理: 見出し, 本文
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""

        # 見出し
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": heading}}]},
        })

        # 本文
        if body:
            for para in body.split("\n"):
                para = para.strip()
                if para:
                    blocks.append(_paragraph_block(para))

    return blocks
