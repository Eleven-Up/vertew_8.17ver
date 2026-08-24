"""Quick end-to-end check of the Vertew conversation loop (menu Q&A).

Run the backend first (python -m uvicorn main:app), then in a second terminal
with the venv active:  python test_chat.py
"""
import asyncio
import json

import websockets

QUESTIONS = [
    "What's in this mango?",
    "How spicy is the watermelon?",
    "Is this halal?",
    "Can I pay by card?",
    "Does anything have cilantro in it?",   # not on the menu -> generation/escalation
]

URL = "ws://127.0.0.1:8000/ws"


async def main():
    for q in QUESTIONS:
        try:
            async with websockets.connect(URL) as ws:
                await ws.send(json.dumps({"transcript": q}))
                r = json.loads(await ws.recv())
            action = r.get("action", "answer")
            tag = "  [🙋 사장님 호출]" if action == "call_owner" else ""
            print(f"\n👤 {q}\n🐦 {r['text']}  ({r.get('emotion')}/{r.get('gesture')}){tag}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n👤 {q}\n⚠️  실패: {exc!r}  (서버가 8000 포트로 떠 있는지 확인)")


if __name__ == "__main__":
    asyncio.run(main())