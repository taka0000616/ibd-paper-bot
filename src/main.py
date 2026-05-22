"""
IBD Paper Bot - エントリーポイント

フロー:
1. PubMed検索 (過去6ヶ月、信頼度高めの研究タイプに絞る)
2. ジャーナルホワイトリスト + IF閾値でフィルタ
3. スコア降順にソートし上位20件
4. 各論文をNotion DBで重複チェック
5. 重複なしの論文をClaude APIで要約
6. Notion DBに投稿
"""
import sys
import time
import traceback

import config
import pubmed
import filter as paper_filter
import ai_summarize
import notion_writer


def main():
    print("=" * 60)
    print("IBD Paper Bot started")
    print(f"  Search window: past {config.PUBMED_RELDATE_DAYS} days")
    print(f"  Max per run  : {config.PAPERS_PER_RUN_MAX}")
    print(f"  Min IF       : {config.MIN_IF_THRESHOLD}")
    print("=" * 60)

    # Step 1: PubMed検索
    try:
        pmids = pubmed.search_recent_papers()
    except Exception as e:
        print(f"[FATAL] PubMed search failed: {e}")
        sys.exit(1)

    if not pmids:
        print("[INFO] No papers found. Exit.")
        return

    # Step 2: 詳細取得
    try:
        papers = pubmed.fetch_paper_details(pmids)
        print(f"[PubMed] Fetched {len(papers)} paper details")
    except Exception as e:
        print(f"[FATAL] PubMed fetch failed: {e}")
        sys.exit(1)

    # Step 3: フィルタ & スコアリング
    ranked_papers = paper_filter.filter_and_rank(papers)

    if not ranked_papers:
        print("[INFO] No papers passed filter. Exit.")
        return

    print(f"\n[INFO] Top {len(ranked_papers)} papers to process:")
    for i, p in enumerate(ranked_papers, 1):
        print(f"  {i}. [{p['score']:.1f}] {p['journal_iso']} - {p['title'][:80]}")
    print()

    # Step 4-6: 各論文を処理
    stats = {"added": 0, "duplicates": 0, "errors": 0, "skipped_low_importance": 0}

    for paper in ranked_papers:
        pmid = paper["pmid"]
        title_short = paper["title"][:60]

        try:
            # 重複チェック
            if notion_writer.is_pmid_exists(pmid):
                print(f"[SKIP-DUP] PMID {pmid}: {title_short}")
                stats["duplicates"] += 1
                continue

            # AI要約
            print(f"[SUMMARIZE] PMID {pmid}: {title_short}")
            summary = ai_summarize.summarize_paper(paper)
            if summary is None:
                print(f"  → summarize failed, skip")
                stats["errors"] += 1
                continue

            # 低Importanceスキップ判定
            importance = summary.get("importance", "★")
            if config.SKIP_LOW_IMPORTANCE and importance == "★":
                print(f"  → skipped (low importance ★)")
                stats["skipped_low_importance"] += 1
                continue

            # Notion投稿
            page_url = notion_writer.create_paper_page(paper, summary)
            if page_url:
                print(f"  → ADDED: {page_url} [Importance: {importance}]")
                stats["added"] += 1
            else:
                print(f"  → Notion create failed")
                stats["errors"] += 1

            # レート制限対策
            time.sleep(1)

        except Exception as e:
            print(f"[ERROR] PMID {pmid}: {e}")
            traceback.print_exc()
            stats["errors"] += 1
            continue

    # サマリ
    print("\n" + "=" * 60)
    print("Run summary:")
    print(f"  Added              : {stats['added']}")
    print(f"  Duplicates skipped : {stats['duplicates']}")
    print(f"  Low importance     : {stats['skipped_low_importance']}")
    print(f"  Errors             : {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
