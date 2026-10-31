"""バッチ要約フローの検証(APIには一切つながない)。

実行: PYTHONPATH=src python3 tests/test_batch_flow.py
確認する振る舞い:
  1. バッチ経路が通り、Notionに書かれる
  2. バッチが使えないときは同期呼び出しに退避する
  3. 時間内に終わらなければIDを保存し、次回の実行で回収する
  4. 要約キャッシュがあればAPIに投げない
"""
import json
import os
import shutil
import sys

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402

config.SUMMARY_CACHE_DIR = "/tmp/ibd-bot-test-cache"

import ai_summarize  # noqa: E402
import batch  # noqa: E402
import main as bot_main  # noqa: E402
import notion_writer  # noqa: E402
import pubmed  # noqa: E402
import filter as paper_filter  # noqa: E402

SUMMARY_JSON = json.dumps({
    "title_jp": "テスト論文",
    "key_findings": "**背景・目的**: テスト\n\n**方法**: テスト\n\n**結果**: テスト\n\n**結論**: テスト",
    "ai_summary": "【テスト論文】テスト用の要約。",
    "study_type": "RCT",
    "importance": "★★",
    "importance_reason": "テスト",
    "relevance_tags": ["その他"],
}, ensure_ascii=False)


def _paper(pmid, doi=None):
    return {
        "pmid": pmid, "title": f"Paper {pmid}", "abstract": "A" * 1200,
        "journal_iso": "Gut", "pub_date": "2026-01-01", "pub_types": ["Journal Article"],
        "doi": doi or f"10.1000/{pmid}", "authors": "Test T", "pmcid": "",
        "estimated_if": 24.0, "score": 50.0, "journal_select": "Gut", "category": ["UC"],
        "study_type": "RCT",
    }


def _install_stubs(monkey, papers, posted):
    monkey(pubmed, "search_recent_papers", lambda: [p["pmid"] for p in papers])
    monkey(pubmed, "fetch_paper_details", lambda pmids: list(papers))
    monkey(pubmed, "fetch_full_text", lambda pmcid: None)
    monkey(paper_filter, "filter_and_rank", lambda ps: list(ps))
    monkey(notion_writer, "get_existing_keys", lambda: (set(), set()))
    monkey(notion_writer, "create_paper_page",
           lambda paper, summary: posted.append((paper["pmid"], summary["importance"]))
           or f"https://notion/{paper['pmid']}")
    monkey(bot_main.time, "sleep", lambda *_: None)
    monkey(batch.time, "sleep", lambda *_: None)


def run_case(name, papers, batch_behaviour, expect_posted, expect_pending,
             preset_cache=None, keep_state=False):
    if not keep_state:
        # keep_state=True のケースは直前のケースが残した pending_batch.json を使う
        shutil.rmtree(config.SUMMARY_CACHE_DIR, ignore_errors=True)
    if preset_cache:
        for pmid in preset_cache:
            ai_summarize.cache_save(pmid, False, json.loads(SUMMARY_JSON))

    saved = []
    posted = []
    calls = {"submit": 0, "sync": 0}

    def monkey(mod, attr, value):
        saved.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, value)

    _install_stubs(monkey, papers, posted)

    if batch_behaviour == "ok":
        monkey(batch, "submit", lambda items: calls.__setitem__("submit", len(items)) or "msgbatch_1")
        monkey(batch, "wait_until_ended", lambda bid, **kw: {"processing_status": "ended",
                                                             "results_url": "x"})
        monkey(batch, "fetch_results",
               lambda info: {f"pmid-{p['pmid']}": SUMMARY_JSON for p in papers})
    elif batch_behaviour == "unavailable":
        monkey(batch, "submit", lambda items: None)
        monkey(ai_summarize, "summarize_paper",
               lambda paper: calls.__setitem__("sync", calls["sync"] + 1)
               or json.loads(SUMMARY_JSON))
    elif batch_behaviour == "timeout":
        monkey(batch, "submit", lambda items: calls.__setitem__("submit", len(items)) or "msgbatch_2")
        monkey(batch, "wait_until_ended", lambda bid, **kw: None)
    elif batch_behaviour == "collect_pending":
        monkey(batch, "get_status", lambda bid: {"processing_status": "ended", "results_url": "x"})
        monkey(batch, "fetch_results",
               lambda info: {f"pmid-{p['pmid']}": SUMMARY_JSON for p in papers})
        monkey(batch, "submit", lambda items: None)
        monkey(ai_summarize, "summarize_paper", lambda paper: None)

    try:
        bot_main.main()
    finally:
        for mod, attr, old in reversed(saved):
            setattr(mod, attr, old)

    pending = batch.load_pending()
    ok = True
    if sorted(p[0] for p in posted) != sorted(expect_posted):
        print(f"  FAIL posted={sorted(p[0] for p in posted)} expected={sorted(expect_posted)}")
        ok = False
    if bool(pending) != expect_pending:
        print(f"  FAIL pending={bool(pending)} expected={expect_pending}")
        ok = False
    print(f"{'PASS' if ok else 'FAIL'}  {name}  (posted={len(posted)}, "
          f"batch_submitted={calls['submit']}, sync_calls={calls['sync']}, pending={bool(pending)})")
    return ok, calls, pending


def main():
    results = []
    papers = [_paper("111"), _paper("222")]

    print("\n--- 1. バッチ経路が通る ---")
    ok, calls, _ = run_case("batch path", papers, "ok", ["111", "222"], False)
    results.append(ok and calls["submit"] == 2 and calls["sync"] == 0)

    print("\n--- 2. バッチ不可なら同期に退避 ---")
    ok, calls, _ = run_case("sync fallback", papers, "unavailable", ["111", "222"], False)
    results.append(ok and calls["sync"] == 2)

    print("\n--- 3. 時間切れならIDを保存して次回に持ち越し ---")
    ok, calls, pending = run_case("timeout -> pending", papers, "timeout", [], True)
    has_id = bool(pending and pending.get("batch_id") == "msgbatch_2")
    no_fulltext = bool(pending and all("full_text" not in p for p in pending["papers"].values()))
    print(f"      batch_id保存={has_id}, 全文は保存しない={no_fulltext}")
    results.append(ok and has_id and no_fulltext)

    print("\n--- 4. 持ち越したバッチを次回の冒頭で回収(二重投稿しない) ---")
    # 3 の state を残したまま実行。回収で投稿した論文を、続く走査で
    # もう一度投稿しないこと(Notion照会の反映遅延に依存しない保護)
    ok, calls, pending = run_case("collect pending", papers, "collect_pending",
                                  ["111", "222"], False, keep_state=True)
    results.append(ok)

    print("\n--- 5. キャッシュがあればAPIに投げない ---")
    ok, calls, _ = run_case("cache hit", papers, "unavailable", ["111", "222"], False,
                            preset_cache=["111", "222"])
    print(f"      submit={calls['submit']}, sync={calls['sync']} (両方0なら課金なし)")
    results.append(ok and calls["submit"] == 0 and calls["sync"] == 0)

    shutil.rmtree(config.SUMMARY_CACHE_DIR, ignore_errors=True)
    print("\n" + "=" * 50)
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
