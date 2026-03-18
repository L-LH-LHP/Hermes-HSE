#!/usr/bin/env python3
"""
子进程搜索 worker：在独立进程中执行 C++ 搜索，崩溃时仅子进程退出，主进程 Flask 保持运行。
从 stdin 读取一行 JSON：{"keyword": "...", "writer_ids": [0,1,...]}
向 stdout 输出一行 JSON：搜索结果或 {"error": "..."}；失败时 exit(1)。
"""
import sys
import json
import os

def main():
    web_api_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(web_api_dir)
    if web_api_dir not in sys.path:
        sys.path.insert(0, web_api_dir)

    try:
        raw = sys.stdin.readline()
        if not raw:
            print(json.dumps({"error": "No input"}), file=sys.stderr)
            sys.exit(1)
        data = json.loads(raw)
        keyword = data.get("keyword", "").strip()
        writer_ids = data.get("writer_ids")
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    try:
        from config import HERMES_SERVER, HERMES_NUM_WRITERS, HERMES_EPOCH
        from hermes_python_client import HermesClient
    except ImportError as e:
        print(json.dumps({"error": f"Import failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    # 子进程仅向 stdout 输出一行 JSON，禁止 client 初始化等打印到 stdout
    os.environ["HERMES_QUIET"] = "1"

    try:
        client = HermesClient(
            server_address=HERMES_SERVER,
            num_writers=HERMES_NUM_WRITERS,
            epoch=HERMES_EPOCH,
        )
        result = client.search(keyword, writer_ids)
        print(json.dumps(result))
        if hasattr(client, "cleanup"):
            client.cleanup()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
