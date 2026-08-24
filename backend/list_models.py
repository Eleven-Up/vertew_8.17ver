"""List models available from the factchat/CNU API Gateway.

Run from backend/ with the venv python:
    .venv\\Scripts\\python.exe list_models.py
"""
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv(".env")

key = os.environ.get("FACTCHAT_API_KEY", "")
base = (os.environ.get("FACTCHAT_BASE_URL")
        or "https://factchat-cloud.mindlogic.ai/v1/gateway").rstrip("/")

print(f"base_url : {base}")
print(f"key      : {'set (' + str(len(key)) + ' chars)' if key else 'MISSING'}")
print("-" * 60)

try:
    r = httpx.get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    print("status:", r.status_code)
    if r.status_code != 200:
        print("body  :", r.text[:2000])
    else:
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        ids = [item.get("id", item) if isinstance(item, dict) else item for item in items]
        print(f"{len(ids)} models:")
        for mid in ids:
            print(" -", mid)
        print("\nraw JSON:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
except Exception as exc:
    print("REQUEST FAILED:", repr(exc))
