"""
PubMed E-utilities ラッパー
esearch → efetch で論文メタデータを取得
"""
import time
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
from datetime import datetime

import config

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _build_params(**kwargs) -> dict:
    """API共通パラメータ"""
    params = {"tool": "ibd-paper-bot", "email": config.PUBMED_EMAIL}
    if config.PUBMED_API_KEY:
        params["api_key"] = config.PUBMED_API_KEY
    params.update(kwargs)
    return params


def search_recent_papers() -> List[str]:
    """過去N日間に登録されたIBD関連論文のPMIDを取得"""
    params = _build_params(
        db="pubmed",
        term=config.PUBMED_QUERY,
        reldate=config.PUBMED_RELDATE_DAYS,
        datetype="pdat",  # publication date
        retmode="json",
        retmax=200,  # 検索段階では多めに取得、後段でフィルタ
        sort="pub_date",
    )

    resp = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    pmids = data.get("esearchresult", {}).get("idlist", [])
    print(f"[PubMed] Search returned {len(pmids)} PMIDs")
    return pmids


def fetch_paper_details(pmids: List[str]) -> List[Dict]:
    """PMIDリストから論文詳細を取得"""
    if not pmids:
        return []

    # 一度に200件まで取得可能。今回は20件上限なので問題ない
    params = _build_params(
        db="pubmed",
        id=",".join(pmids),
        retmode="xml",
    )

    resp = requests.post(f"{BASE_URL}/efetch.fcgi", data=params, timeout=60)
    resp.raise_for_status()

    return _parse_efetch_xml(resp.text)


def _parse_efetch_xml(xml_text: str) -> List[Dict]:
    """efetchのXMLレスポンスをパース"""
    root = ET.fromstring(xml_text)
    papers = []

    for article in root.findall(".//PubmedArticle"):
        try:
            paper = _extract_paper_data(article)
            if paper:
                papers.append(paper)
        except Exception as e:
            print(f"[PubMed] Parse error: {e}")
            continue

    return papers


def _extract_paper_data(article: ET.Element) -> Optional[Dict]:
    """単一の<PubmedArticle>から構造化データを抽出"""
    pmid_elem = article.find(".//PMID")
    if pmid_elem is None:
        return None
    pmid = pmid_elem.text

    # タイトル
    title_elem = article.find(".//ArticleTitle")
    title = _get_full_text(title_elem) if title_elem is not None else ""

    # アブストラクト(複数AbstractTextがある場合は連結)
    abstract_parts = []
    for abst in article.findall(".//Abstract/AbstractText"):
        label = abst.get("Label", "")
        text = _get_full_text(abst)
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = "\n".join(abstract_parts)

    # ジャーナル(ISO略称)
    journal_iso = article.findtext(".//Journal/ISOAbbreviation", "")
    journal_full = article.findtext(".//Journal/Title", "")

    # 発行日
    pub_date = _extract_pub_date(article)

    # Publication Types
    pub_types = [pt.text for pt in article.findall(".//PublicationType") if pt.text]

    # 著者(第一著者と責任著者)
    authors = _extract_authors(article)

    # DOI
    doi = ""
    for id_elem in article.findall(".//ArticleId"):
        if id_elem.get("IdType") == "doi":
            doi = id_elem.text or ""
            break

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal_iso": journal_iso,
        "journal_full": journal_full,
        "pub_date": pub_date,
        "pub_types": pub_types,
        "authors": authors,
        "doi": doi,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def _get_full_text(elem: ET.Element) -> str:
    """要素のテキスト全体を取得(子要素のテキストも含む)"""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _extract_pub_date(article: ET.Element) -> Optional[str]:
    """発行日を ISO 形式(YYYY-MM-DD)で返す。月日が無ければ可能な範囲で"""
    # ArticleDate(電子発行日)を優先
    article_date = article.find(".//ArticleDate")
    if article_date is not None:
        y = article_date.findtext("Year")
        m = article_date.findtext("Month") or "01"
        d = article_date.findtext("Day") or "01"
        if y:
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

    # フォールバック: PubDate
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is not None:
        y = pub_date.findtext("Year")
        m = pub_date.findtext("Month") or "01"
        d = pub_date.findtext("Day") or "01"
        # MonthがJan/Feb等の場合変換
        month_map = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
            "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
            "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
        }
        m = month_map.get(m, m)
        if y and m.isdigit():
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

    return None


def _extract_authors(article: ET.Element) -> str:
    """第一著者と最終著者を取得して文字列化"""
    authors_list = article.findall(".//AuthorList/Author")
    if not authors_list:
        return ""

    def format_author(author: ET.Element) -> str:
        last = author.findtext("LastName", "")
        initials = author.findtext("Initials", "")
        return f"{last} {initials}".strip()

    first = format_author(authors_list[0])
    if len(authors_list) == 1:
        return first
    last = format_author(authors_list[-1])
    return f"{first} ... {last}" if len(authors_list) > 2 else f"{first}, {last}"
