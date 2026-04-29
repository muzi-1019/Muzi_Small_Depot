"""
从本服务批量调用 /api/chat，导出一批可用于 RAGAS 的 JSONL 行（question/contexts/answer）。

用法（需服务已启动且存在合法 user_id / character_id）:
  python scripts/export_ragas_rows.py --base-url http://127.0.0.1:8000 --user-id 1 --character-id 2 --questions-file questions.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--character-id", type=int, required=True)
    parser.add_argument("--questions-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("ragas_rows.jsonl"))
    args = parser.parse_args()

    questions = [q.strip() for q in args.questions_file.read_text(encoding="utf-8").splitlines() if q.strip()]
    url = f"{args.base_url.rstrip('/')}/api/chat"
    rows: list[dict] = []
    with httpx.Client(timeout=120.0) as client:
        for q in questions:
            resp = client.post(
                url,
                json={"user_id": args.user_id, "character_id": args.character_id, "question": q},
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload["data"]
            rows.append(
                {
                    "question": q,
                    "contexts": data.get("retrieve_knowledge") or [],
                    "answer": data.get("answer") or "",
                }
            )

    with args.out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
