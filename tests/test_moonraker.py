"""Tests for the fake Moonraker server."""

from __future__ import annotations

import asyncio
import socket

import aiohttp
import pytest

from breathbridge.moonraker_server import MoonrakerServer, MoonrakerState


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server():
    state = MoonrakerState()
    port = _free_port()
    srv = MoonrakerServer("127.0.0.1", port, state)
    await srv.start()
    try:
        yield state, port
    finally:
        await srv.stop()


async def _rpc(ws: aiohttp.ClientWebSocketResponse, method: str, params=None, rid: int = 1):
    msg = {"jsonrpc": "2.0", "method": method, "id": rid}
    if params is not None:
        msg["params"] = params
    await ws.send_json(msg)
    while True:
        reply = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
        if reply.get("id") == rid:
            return reply


async def test_server_info_http(server):
    _state, port = server
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/server/info") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["result"]["klippy_state"] == "ready"
            assert data["result"]["klippy_connected"] is True


async def test_printer_info_http(server):
    _state, port = server
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/printer/info") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["result"]["state"] == "ready"


async def test_objects_query_http(server):
    state, port = server
    await state.update(temp=55.5, target=60.0)
    async with aiohttp.ClientSession() as session:
        url = f"http://127.0.0.1:{port}/printer/objects/query?heater_bed=temperature,target"
        async with session.get(url) as resp:
            data = await resp.json()
    bed = data["result"]["status"]["heater_bed"]
    assert bed["temperature"] == 55.5
    assert bed["target"] == 60.0


async def test_ws_handshake_and_query(server):
    state, port = server
    await state.update(temp=42.0, target=50.0)
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/websocket") as ws:
            identify = await _rpc(
                ws,
                "server.connection.identify",
                {"client_name": "test", "version": "1", "type": "web", "url": "x"},
                rid=1,
            )
            assert "connection_id" in identify["result"]

            info = await _rpc(ws, "server.info", rid=2)
            assert info["result"]["klippy_state"] == "ready"

            objs = await _rpc(ws, "printer.objects.list", rid=3)
            assert "heater_bed" in objs["result"]["objects"]

            q = await _rpc(
                ws,
                "printer.objects.query",
                {"objects": {"heater_bed": ["temperature", "target"]}},
                rid=4,
            )
            bed = q["result"]["status"]["heater_bed"]
            assert bed == {"temperature": 42.0, "target": 50.0}


async def test_ws_subscribe_emits_notification(server):
    state, port = server
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/websocket") as ws:
            # Initial state
            await state.update(temp=20.0, target=0.0)

            sub_reply = await _rpc(
                ws,
                "printer.objects.subscribe",
                {"objects": {"heater_bed": ["temperature", "target"]}},
                rid=10,
            )
            assert sub_reply["result"]["status"]["heater_bed"]["temperature"] == 20.0

            # Trigger an update — should arrive as notify_status_update
            await state.update(temp=65.0, target=60.0)

            for _ in range(5):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                if msg.get("method") == "notify_status_update":
                    params = msg["params"]
                    assert "heater_bed" in params[0]
                    assert params[0]["heater_bed"]["temperature"] == 65.0
                    assert params[0]["heater_bed"]["target"] == 60.0
                    break
            else:
                pytest.fail("Did not receive notify_status_update")


async def test_ws_unknown_method_returns_error(server):
    _state, port = server
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/websocket") as ws:
            reply = await _rpc(ws, "totally.fake.method", rid=99)
            assert reply["error"]["code"] == -32601
