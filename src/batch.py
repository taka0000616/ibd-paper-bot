"""
Message Batches API で要約をまとめて実行する。

1日1回の cron で動かしており結果の即時性は不要なので、同期呼び出し
(/v1/messages)ではなくバッチ(/v1/messages/batches)を使う。
入力・出力トークンの単価が一律 50% になる。

バッチは投入から完了までに時間がかかる(通常は数分、仕様上は最大24時間)。
実行時間内に終わらなかった場合はバッチIDを state ファイルに残して終了し、
次回の実行の冒頭で回収する。こうしないと「課金は済んでいるのに
Notion には何も書かれない」という取りこぼしが起きる。
"""
import json
import os
import time

import requests
from typing import Dict, List, Optional

import config

BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# この状態コードはバッチAPI自体が使えないことを意味するので、同期呼び出しに退避する
_UNAVAILABLE_STATUS = {400, 401, 403, 404, 405}


def _headers() -> Dict[str, str]:
    return {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _request(method: str, url: str, **kwargs) -> Optional[requests.Response]:
    """一時的な失敗はリトライし、恒久的な失敗は None を返す"""
    for attempt in range(1, 4):
        try:
            resp = requests.request(method, url, headers=_headers(), timeout=120, **kwargs)
        except requests.RequestException as e:
            if attempt < 3:
                wait = 2 ** attempt * 5
                print(f"[batch] {method} {url} failed ({e}), retry in {wait}s")
                time.sleep(wait)
                continue
            print(f"[batch] {method} {url} failed permanently: {e}")
            return None
        if resp.status_code in _RETRYABLE_STATUS and attempt < 3:
            wait = 2 ** attempt * 5
            print(f"[batch] status {resp.status_code}, retry in {wait}s")
            time.sleep(wait)
            continue
        return resp
    return None


# ------------------------------------------------------------------
# 投入 / 監視 / 回収
# ------------------------------------------------------------------
def submit(items: List[Dict]) -> Optional[str]:
    """要約リクエストをまとめて投入し、バッチIDを返す。

    items: [{"custom_id": str, "prompt": str, "max_tokens": int}, ...]
    バッチAPIが使えない場合は None(呼び出し側は同期呼び出しに退避する)。
    """
    if not items:
        return None
    payload = {
        "requests": [
            {
                "custom_id": it["custom_id"],
                "params": {
                    "model": config.CLAUDE_MODEL,
                    "max_tokens": it["max_tokens"],
                    "messages": [{"role": "user", "content": it["prompt"]}],
                },
            }
            for it in items
        ]
    }
    resp = _request("POST", BATCHES_URL, json=payload)
    if resp is None:
        return None
    if resp.status_code in _UNAVAILABLE_STATUS:
        print(f"[batch] batch API unavailable (status {resp.status_code}): {resp.text[:200]}")
        return None
    if resp.status_code >= 300:
        print(f"[batch] submit failed (status {resp.status_code}): {resp.text[:300]}")
        return None
    batch_id = resp.json().get("id")
    print(f"[batch] submitted {len(items)} requests -> {batch_id}")
    return batch_id


def get_status(batch_id: str) -> Optional[Dict]:
    """バッチの状態を返す。{"processing_status":..., "request_counts":{...}, "results_url":...}"""
    resp = _request("GET", f"{BATCHES_URL}/{batch_id}")
    if resp is None or resp.status_code >= 300:
        if resp is not None:
            print(f"[batch] status check failed ({resp.status_code}): {resp.text[:200]}")
        return None
    return resp.json()


def wait_until_ended(batch_id: str, max_wait_sec: int, poll_sec: int) -> Optional[Dict]:
    """完了するまで待つ。時間内に終わらなければ None を返す(= 次回に回収)"""
    waited = 0
    while waited <= max_wait_sec:
        info = get_status(batch_id)
        if info is None:
            return None
        status = info.get("processing_status")
        counts = info.get("request_counts", {})
        print(f"[batch] {batch_id}: {status} {counts} (waited {waited}s)")
        if status == "ended":
            return info
        time.sleep(poll_sec)
        waited += poll_sec
    print(f"[batch] not finished within {max_wait_sec}s, will collect on the next run")
    return None


def fetch_results(info: Dict) -> Dict[str, Optional[str]]:
    """完了したバッチの結果を {custom_id: 出力テキスト or None} で返す"""
    url = info.get("results_url")
    if not url:
        print("[batch] results_url missing")
        return {}
    resp = _request("GET", url)
    if resp is None or resp.status_code >= 300:
        print("[batch] results download failed")
        return {}

    out: Dict[str, Optional[str]] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = rec.get("custom_id")
        result = rec.get("result") or {}
        rtype = result.get("type")
        if rtype == "succeeded":
            try:
                out[cid] = result["message"]["content"][0]["text"]
            except (KeyError, IndexError, TypeError):
                print(f"[batch] {cid}: unexpected message shape")
                out[cid] = None
        else:
            # errored / canceled / expired
            err = (result.get("error") or {}).get("message", rtype)
            print(f"[batch] {cid}: {rtype} ({err})")
            out[cid] = None
    return out


# ------------------------------------------------------------------
# 未完了バッチの持ち越し
# ------------------------------------------------------------------
def _state_path() -> str:
    return os.path.join(config.SUMMARY_CACHE_DIR, "pending_batch.json")


def save_pending(batch_id: str, papers: Dict[str, Dict]) -> None:
    """実行時間内に終わらなかったバッチを次回に引き継ぐ。

    papers は custom_id -> 論文dict。全文は再取得できるうえ巨大なので捨てる。
    """
    slim = {}
    for cid, p in papers.items():
        slim[cid] = {k: v for k, v in p.items() if k != "full_text"}
    try:
        os.makedirs(config.SUMMARY_CACHE_DIR, exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump({"batch_id": batch_id, "saved_at": time.time(), "papers": slim},
                      f, ensure_ascii=False)
        print(f"[batch] pending batch {batch_id} saved ({len(slim)} papers)")
    except OSError as e:  # noqa: BLE001
        print(f"[batch] could not save pending batch: {e}")


def load_pending() -> Optional[Dict]:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def clear_pending() -> None:
    try:
        os.remove(_state_path())
    except OSError:
        pass
