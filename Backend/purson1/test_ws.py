import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://127.0.0.1:8000/ws/analyze"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as ws:
            print("Connected!")
            await ws.send(json.dumps({"paper_input": "10.1145/1234.5678"}))
            print("Sent data. Waiting for response...")
            res = await ws.recv()
            print("Received:", res)
    except Exception as e:
        print(f"Error: {e}")
        if hasattr(e, "response"):
            print("Response details:", getattr(e, "response", None))

if __name__ == "__main__":
    asyncio.run(test_ws())
