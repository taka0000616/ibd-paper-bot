"""
IBD Paper Bot - エントリーポイント

フロー:
1. PubMed検索 (過去6ヶ月、信頼度高めの研究タイプに絞る)
2. ジャーナルホワイトリスト + IF閾値でフィルタ
3. スコア降順にソートし上位20件
4. 各論文をNotion DBで重複チェック
5. 重複なしの論文をClaude APIで要約(既定はMessage Batchesでまとめて実行)
6. Notion DBに投稿

要約はバッチAPIで行う(単価が一律50%)。実行時間内に終わらなかったバッチは
IDを保存して次回の実行の冒頭で回収するため、課金済みの結果を取りこぼさない。
"""
import sys
import time
import traceback

import config
import pubmed
import filter as paper_filter
import ai_summarize
import notion_writer
import batch


def _post_summary(paper, summary, stats) -> bool:
    """Importance判定 → Notion投稿。追加できたら True"""
    importance = summary.get("importance", "★")
    if config.SKIP_LOW_IMPORTANCE and importance == "★":
        print(f"  → skipped (low importance ★): PMID {paper['pmid']}")
        stats["skipped_low_importance"] += 1
        return False

    page_url = notion_writer.create_paper_page(paper, summary)
    if page_url:
        print(f"  → ADDED: {page_url} [Importance: {importance}] PMID {paper['pmid']}")
        stats["added"] += 1
        stats["posted_pmids"].add(paper["pmid"])
        time.sleep(1)  # Notion レート制限対策
        return True

    print(f"  → Notion create failed: PMID {paper['pmid']}")
    stats["errors"] += 1
    return False


def _write_batch_results(results, papers, stats) -> None:
    """バッチの出力テキストを要約として解釈し、Notionに書く"""
    for cid, text in results.items():
        paper = papers.get(cid)
        if paper is None:
            print(f"[WARN] unknown custom_id in results: {cid}")
            continue
        if text is None:
            stats["errors"] += 1
            continue
        summary = ai_summarize.parse_response(text, paper["pmid"])
        if summary is None:
            stats["errors"] += 1
            continue
        has_ft = bool(paper.get("has_full_text"))
        summary["_source"] = "fulltext" if has_ft else "abstract"
        # Notion書き込みが失敗しても要約は再利用できるよう先に保存する
        ai_summarize.cache_save(paper["pmid"], has_ft, summary)
        _post_summary(paper, summary, stats)


def _collect_pending_batch(stats) -> None:
    """前回の実行で完了を待てなかったバッチを回収する。

    これをしないと「APIには課金済みなのにNotionには何も書かれていない」
    という取りこぼしが起きる。
    """
    state = batch.load_pending()
    if not state:
        return
    batch_id = state.get("batch_id")
    papers = state.get("papers") or {}
    print(f"\n[INFO] Pending batch from a previous run: {batch_id} ({len(papers)} papers)")

    info = batch.get_status(batch_id)
    if info is None:
        print("[WARN] Could not query the pending batch, keeping it for the next run")
        return
    if info.get("processing_status") != "ended":
        print(f"[INFO] Still {info.get('processing_status')}, keeping it for the next run")
        return

    _write_batch_results(batch.fetch_results(info), papers, stats)
    batch.clear_pending()


def _scan_and_summarize(stats) -> None:
    """PubMed検索からNotion投稿までの本体。候補が無ければ早期に戻る"""
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

    # Step 3: フィルタ & スコアリング(上限なし、スコア順全件返却)
    ranked_papers = paper_filter.filter_and_rank(papers)

    if not ranked_papers:
        print("[INFO] No papers passed filter. Exit.")
        return

    print(f"\n[INFO] {len(ranked_papers)} candidates after filter, "
          f"will process up to {config.PAPERS_PER_RUN_MAX} new papers\n")

    # Step 3.5: 既存PMID/DOIを一括取得し、候補から事前除外する。
    # これをしないと、スコア上位を占める高IF誌の論文(すべて登録済み)を
    # 毎回 target×10 件走査して打ち切り、新着の中堅誌論文に到達できない。
    existing_pmids, existing_dois = notion_writer.get_existing_keys()
    use_preloaded = existing_pmids is not None
    if use_preloaded:
        before = len(ranked_papers)
        # PMID だけでなく DOI でも除外する。
        # ahead-of-print と確定版は PMID が別なので、DOI を見ないと
        # 同じ論文を二重に登録し、要約APIにも二重に課金してしまう。
        dois = existing_dois or set()
        ranked_papers = [
            p for p in ranked_papers
            if p["pmid"] not in existing_pmids
            and notion_writer.normalize_doi(p.get("doi", "")) not in dois
        ]
        print(f"[INFO] Excluded {before - len(ranked_papers)} already-posted papers up front "
              f"(PMID+DOI), {len(ranked_papers)} new candidates remain\n")
        if not ranked_papers:
            print("[INFO] No new papers to add. Exit.")
            return
    else:
        print("[WARN] Could not preload existing PMIDs, falling back to per-paper dedup\n")

    target = config.PAPERS_PER_RUN_MAX
    # ★止まりでスキップされる分を見込み、バッチには目標より多めに積む。
    # バッチは投入後に取り消せないため、実際の追加件数は target を
    # 多少上回ることがある(課金済みの要約を捨てないほうが得なので許容する)。
    want = int(target * config.BATCH_OVERSHOOT + 0.5) if config.USE_BATCH_API else target

    # Step 4: 候補を選び、全文を取得する。
    # 要約済み(キャッシュ有り)はその場でNotionへ、残りはバッチにまとめる。
    batch_items = []
    batch_papers = {}

    for paper in ranked_papers:
        if stats["added"] + len(batch_items) >= want:
            print(f"\n[INFO] Collected {len(batch_items)} papers to summarize "
                  f"(added {stats['added']} from cache), stop scanning")
            break

        # 安全装置: スコア下位まで走り過ぎないよう、10倍の候補数で打ち切り
        if stats["examined"] >= target * 10:
            print(f"\n[INFO] Examined {stats['examined']} candidates, stopping for safety")
            break

        stats["examined"] += 1
        pmid = paper["pmid"]
        title_short = paper["title"][:60]

        # この実行で既に投稿したもの(持ち越しバッチの回収分)は飛ばす
        if pmid in stats["posted_pmids"]:
            stats["duplicates"] += 1
            continue

        # abstract が実質無い文献(E-Videos・research letter・学会抄録など)は
        # 要約しようがないのに API に課金していた。PMC全文も無ければ捨てる。
        abstract = (paper.get("abstract") or "").strip()
        if len(abstract) < config.MIN_ABSTRACT_CHARS and not paper.get("pmcid"):
            print(f"[SKIP-NOABS] [{stats['examined']:>3}] PMID {pmid} "
                  f"(abstract {len(abstract)} chars, no PMC): {title_short}")
            stats["skipped_no_abstract"] += 1
            continue

        try:
            # 重複チェック(事前除外できなかった場合のみ逐次チェックにフォールバック)
            if not use_preloaded and notion_writer.is_pmid_exists(pmid):
                print(f"[SKIP-DUP] [{stats['examined']:>3}] PMID {pmid}: {title_short}")
                stats["duplicates"] += 1
                time.sleep(0.4)  # Notion レート制限対策(3req/sec)
                continue

            print(f"[PREP] [{stats['examined']:>3}] PMID {pmid} "
                  f"(score {paper['score']:.1f}): {title_short}")

            # PMC全文が取れれば全文ベースで要約(取れなければabstractのみ)
            ft = pubmed.fetch_full_text(paper.get("pmcid", ""))
            if ft:
                paper.update(ft, has_full_text=True)
                print(f"  → full text {len(ft['full_text'])} chars"
                      f"{' (trimmed)' if ft['full_text_truncated'] else ''}")

            req = ai_summarize.build_request(paper)

            # 要約キャッシュがあればAPIに投げない(前回Notion書き込みに失敗した分など)
            cached = ai_summarize.cache_load(pmid, req["has_full_text"])
            if cached is not None:
                _post_summary(paper, cached, stats)
                continue

            custom_id = f"pmid-{pmid}"
            batch_items.append({
                "custom_id": custom_id,
                "prompt": req["prompt"],
                "max_tokens": req["max_tokens"],
            })
            batch_papers[custom_id] = paper

        except Exception as e:
            print(f"[ERROR] PMID {pmid}: {e}")
            traceback.print_exc()
            stats["errors"] += 1
            continue

    # Step 5: 要約を実行する
    if batch_items:
        batch_id = batch.submit(batch_items) if config.USE_BATCH_API else None

        if batch_id:
            # 投入直後に保存する。待っている最中にジョブ自体が強制終了
            # (GitHub Actions の timeout-minutes など)されても、
            # バッチIDが残っていれば次回の実行で結果を回収できる。
            batch.save_pending(batch_id, batch_papers)

            info = batch.wait_until_ended(
                batch_id,
                max_wait_sec=config.BATCH_MAX_WAIT_SEC,
                poll_sec=config.BATCH_POLL_INTERVAL_SEC,
            )
            if info is None:
                print("[INFO] Batch kept for the next run")
            else:
                _write_batch_results(batch.fetch_results(info), batch_papers, stats)
                batch.clear_pending()
        else:
            # バッチが使えない(未対応・権限なし等)ので同期呼び出しに退避する
            if config.USE_BATCH_API:
                print("[WARN] Falling back to synchronous API calls (no batch discount)")
            for custom_id, paper in batch_papers.items():
                try:
                    summary = ai_summarize.summarize_paper(paper)
                    if summary is None:
                        print(f"  → summarize failed: PMID {paper['pmid']}")
                        stats["errors"] += 1
                        continue
                    _post_summary(paper, summary, stats)
                except Exception as e:
                    print(f"[ERROR] PMID {paper['pmid']}: {e}")
                    traceback.print_exc()
                    stats["errors"] += 1



def main():
    print("=" * 60)
    print("IBD Paper Bot started")
    print(f"  Search window: past {config.PUBMED_RELDATE_DAYS} days")
    print(f"  Max per run  : {config.PAPERS_PER_RUN_MAX}")
    print(f"  Min IF       : {config.MIN_IF_THRESHOLD}")
    print(f"  Batch API    : {'on' if config.USE_BATCH_API else 'off'}")
    print("=" * 60)

    stats = {"added": 0, "duplicates": 0, "errors": 0, "skipped_low_importance": 0,
             "skipped_no_abstract": 0, "examined": 0,
             # 同一実行内で投稿したPMID。持ち越しバッチを回収した直後に
             # 同じ論文を走査し直しても二重投稿しないようにするための控え。
             # Notion側のDB照会に反映遅延があっても効く。
             "posted_pmids": set()}

    # 前回の実行で待ちきれなかったバッチを最初に回収する。
    # 検索結果が無い・候補が全て既出といった場合でも必ず通るよう、
    # PubMed検索より前に置く(課金済みの要約を取りこぼさないため)。
    _collect_pending_batch(stats)

    _scan_and_summarize(stats)

    # サマリ
    print("\n" + "=" * 60)
    print("Run summary:")
    print(f"  Examined           : {stats['examined']}")
    print(f"  Added              : {stats['added']}")
    print(f"  Duplicates skipped : {stats['duplicates']}")
    print(f"  Low importance     : {stats['skipped_low_importance']}")
    print(f"  No abstract (skip) : {stats['skipped_no_abstract']}")
    print(f"  Errors             : {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
