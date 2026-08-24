"""Diagnose the factchat/CNU gateway response for the real Vertew prompt shape.

Run from backend/ with the venv python:
    .venv\Scripts\python.exe diag_llm.py
"""
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv(".env")

key = os.environ.get("FACTCHAT_API_KEY", "")
base = (os.environ.get("FACTCHAT_BASE_URL")
        or "https://factchat-cloud.mindlogic.ai/v1/gateway").rstrip("/")
model = os.environ.get("FACTCHAT_MODEL") or "gemini-2.5-flash"

print(f"provider env : LLM_PROVIDER={os.environ.get('LLM_PROVIDER')}")
print(f"model        : {model}")
print(f"base_url     : {base}")
print(f"key          : {'set (' + str(len(key)) + ' chars)' if key else 'MISSING'}")
print("-" * 60)

PROMPT = (
    "PERSONA:\nYou are a friendly street-market fruit vendor character.\n\n"
    "STORE AND PRODUCT INFORMATION:\nStore name: Vertew Fresh Fruits\n"
    "Products: MENU: Mango - Price: MYR 5.00. Spice: not spicy. "
    "Ingredients: Fresh mango. Allergens: none declared.\n\n"
    "CUSTOMER MESSAGE:\n이 망고에 뭔가 들어가요?\n\n"
    "INSTRUCTIONS:\n"
    "- Respond with exactly one JSON object and nothing else.\n"
    '- Fields: "text" (non-empty, <=500 chars), "emotion" (one of: angry, happy, '
    'neutral, sad, surprised), "gesture" (one of: idle, nod, point, think, wave).\n'
    "- Reply in the same language the customer used.\n"
)

for max_tokens in (300, 2048):
    print(f"\n=== max_tokens={max_tokens} ===")
    try:
        r = httpx.post(
            f"{base}/chat/completions/",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": PROMPT}],
                  "max_tokens": max_tokens},
            timeout=90,
        )
        print("status :", r.status_code)
        if r.status_code != 200:
            print("body   :", r.text[:500])
            continue
        d = r.json()
        content = d["choices"][0]["message"]["content"]
        print("finish :", d["choices"][0].get("finish_reason"))
        print("usage  :", json.dumps(d.get("usage"), ensure_ascii=False))
        print("content:", repr(content)[:600])
        # Can our parser read it?
        try:
            parsed = json.loads(content)
            print("PARSE  : OK ->", json.dumps(parsed, ensure_ascii=False)[:200])
        except Exception as exc:
            print(f"PARSE  : FAILED ({exc})")
    except Exception as exc:
        print("REQUEST FAILED:", repr(exc))