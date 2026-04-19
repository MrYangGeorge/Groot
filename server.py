import asyncio
import json
from datetime import datetime, UTC
import websockets

# Store the latest received payload in memory
latest_payload = None


async def handler(websocket):
    """
    This function runs whenever an ESP32 client connects.
    It keeps listening for messages from that client.
    """
    global latest_payload

    print("Client connected.")

    try:
        async for message in websocket:
            print("\nRaw message received:")
            print(message)

            try:
                # Convert incoming JSON string into Python dictionary
                data = json.loads(message)

                # Save latest payload
                latest_payload = data

                # Print parsed data nicely
                print("Parsed JSON:")
                print(json.dumps(data, indent=2))

                # Optional acknowledgment back to ESP32
                response = {
                    "status": "received",
                    "server_time": datetime.utcnow().isoformat() + "Z"
                }
                await websocket.send(json.dumps(response))

            except json.JSONDecodeError:
                print("Received invalid JSON.")
                await websocket.send(json.dumps({"status": "invalid_json"}))

    except websockets.ConnectionClosed:
        print("Client disconnected.")


async def main():
    """
    Starts the WebSocket server on port 8765.
    """
    print("Starting WebSocket server on ws://0.0.0.0:8765")
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())