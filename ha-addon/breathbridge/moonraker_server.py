"""Minimal fake Moonraker server.

Implements enough of the Moonraker JSON-RPC + HTTP API for a client (the
Panda Breath ESP32 in Klipper mode) to discover a "heater_bed" object and
read its current/target temperature. Live updates are pushed to subscribed
websocket clients via notify_status_update.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import WSMsgType, web

logger = logging.getLogger(__name__)


_API_VERSION = [1, 5, 0]
_API_VERSION_STRING = "1.5.0"
_MOONRAKER_VERSION = "v0.9.0-panda-breath-fake"
_KLIPPER_VERSION = "v0.12.0-panda-breath-fake"


class MoonrakerState:
    """Shared state — bed temperature + target, plus a startup eventtime."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.bed_temp: float = 0.0
        self.bed_target: float = 0.0
        # Subscribers: each entry is (websocket, requested_fields) where
        # requested_fields is the list of heater_bed attrs to push, or None
        # for "all".
        self._subscribers: dict[int, tuple[web.WebSocketResponse, list[str] | None]] = {}

    @property
    def eventtime(self) -> float:
        return time.monotonic() - self._start

    def heater_bed_status(self, fields: list[str] | None = None) -> dict[str, Any]:
        full = {
            "temperature": round(self.bed_temp, 2),
            "target": round(self.bed_target, 2),
            "power": 1.0 if self.bed_target > 0 else 0.0,
        }
        if fields is None:
            return full
        return {k: v for k, v in full.items() if k in fields}

    def add_subscriber(
        self, ws: web.WebSocketResponse, fields: list[str] | None
    ) -> None:
        self._subscribers[id(ws)] = (ws, fields)

    def drop_subscriber(self, ws: web.WebSocketResponse) -> None:
        self._subscribers.pop(id(ws), None)

    async def update(self, temp: float | None = None, target: float | None = None) -> None:
        changed: dict[str, Any] = {}
        if temp is not None and abs(temp - self.bed_temp) > 0.01:
            self.bed_temp = float(temp)
            changed["temperature"] = round(self.bed_temp, 2)
        if target is not None and abs(target - self.bed_target) > 0.01:
            self.bed_target = float(target)
            changed["target"] = round(self.bed_target, 2)
            changed["power"] = 1.0 if self.bed_target > 0 else 0.0

        if not changed:
            return

        await self._broadcast(changed)

    async def _broadcast(self, changed: dict[str, Any]) -> None:
        eventtime = self.eventtime
        dead: list[int] = []
        for key, (ws, fields) in self._subscribers.items():
            if ws.closed:
                dead.append(key)
                continue
            filtered = (
                changed
                if fields is None
                else {k: v for k, v in changed.items() if k in fields}
            )
            if not filtered:
                continue
            msg = {
                "jsonrpc": "2.0",
                "method": "notify_status_update",
                "params": [{"heater_bed": filtered}, eventtime],
            }
            try:
                await ws.send_json(msg)
            except ConnectionResetError:
                dead.append(key)
        for key in dead:
            self._subscribers.pop(key, None)


class MoonrakerServer:
    def __init__(self, host: str, port: int, state: MoonrakerState) -> None:
        self._host = host
        self._port = port
        self._state = state
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._setup_routes()

    @property
    def state(self) -> MoonrakerState:
        return self._state

    def _setup_routes(self) -> None:
        app = self._app
        app.middlewares.append(self._cors_and_log_middleware)
        app.router.add_get("/websocket", self._ws_handler)
        # REST equivalents — Moonraker exposes both. Some firmware uses HTTP
        # polling instead of subscriptions, so cover both paths.
        app.router.add_get("/server/info", self._http_server_info)
        app.router.add_get("/printer/info", self._http_printer_info)
        app.router.add_get("/printer/objects/list", self._http_objects_list)
        app.router.add_get("/printer/objects/query", self._http_objects_query)
        # Octoprint compatibility — some Klipper-client firmwares hit these
        # instead of /server/info to detect "is this a printer?"
        app.router.add_get("/api/version", self._http_api_version)
        app.router.add_get("/api/server", self._http_api_server)
        app.router.add_get("/api/printer", self._http_api_printer)
        app.router.add_get("/api/login", self._http_api_login)
        app.router.add_post("/api/login", self._http_api_login)
        # Liveness / discovery
        app.router.add_get("/", self._http_root)
        # Catch-all — log anything we don't recognize so we can see what the
        # device is asking for.
        app.router.add_route("*", "/{tail:.*}", self._http_catchall)

    @web.middleware
    async def _cors_and_log_middleware(self, request: web.Request, handler):
        logger.debug(
            "Moonraker HTTP %s %s from %s",
            request.method,
            request.path_qs,
            request.remote,
        )
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as exc:
                resp = exc
        # CORS headers — Moonraker emits these and some firmwares require them
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, DELETE"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp

    # ---------- JSON-RPC method dispatch ----------

    def _result(self, method: str, params: dict | list | None) -> Any:
        if method == "server.connection.identify":
            return {"connection_id": uuid.uuid4().int & 0xFFFFFFFF}
        if method == "server.info":
            return self._server_info()
        if method == "server.config":
            return {"config": {}}
        if method == "printer.info":
            return self._printer_info()
        if method == "printer.objects.list":
            return {"objects": ["heater_bed", "extruder", "toolhead", "print_stats"]}
        if method == "printer.objects.query":
            return self._objects_query(params or {})
        if method == "printer.objects.subscribe":
            # Handled separately because it needs the ws.
            return None
        if method == "machine.system_info":
            return {"system_info": {}}
        raise _MethodNotFound(method)

    def _server_info(self) -> dict:
        return {
            "klippy_connected": True,
            "klippy_state": "ready",
            "components": ["klippy_apis", "websockets"],
            "failed_components": [],
            "registered_directories": [],
            "warnings": [],
            "websocket_count": 1,
            "moonraker_version": _MOONRAKER_VERSION,
            "api_version": _API_VERSION,
            "api_version_string": _API_VERSION_STRING,
        }

    def _printer_info(self) -> dict:
        return {
            "state": "ready",
            "state_message": "Printer is ready",
            "hostname": "panda-breath-fake",
            "klipper_path": "/dev/null",
            "python_path": "/dev/null",
            "process_id": 1,
            "user_id": 0,
            "group_id": 0,
            "log_file": "/dev/null",
            "config_file": "/dev/null",
            "software_version": _KLIPPER_VERSION,
            "cpu_info": "fake",
        }

    def _objects_query(self, params: dict) -> dict:
        objects = params.get("objects", {}) if isinstance(params, dict) else {}
        status: dict[str, Any] = {}
        for name, fields in objects.items():
            if name == "heater_bed":
                status["heater_bed"] = self._state.heater_bed_status(fields)
            else:
                status[name] = {}
        return {"eventtime": self._state.eventtime, "status": status}

    def _extract_heater_bed_fields(self, params: dict) -> list[str] | None:
        objects = params.get("objects", {}) if isinstance(params, dict) else {}
        if "heater_bed" not in objects:
            return None
        fields = objects["heater_bed"]
        if fields is None:
            return None
        return list(fields)

    # ---------- WebSocket handler ----------

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None, autoping=False)
        await ws.prepare(request)
        peer = request.remote
        logger.info("Moonraker WS client connected from %s", peer)

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    req = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("Moonraker WS: invalid JSON: %s", msg.data[:200])
                    continue

                if isinstance(req, list):
                    # Batch — answer each individually
                    for r in req:
                        await self._handle_rpc(ws, r)
                else:
                    await self._handle_rpc(ws, req)
        finally:
            self._state.drop_subscriber(ws)
            logger.info("Moonraker WS client %s disconnected", peer)

        return ws

    async def _handle_rpc(self, ws: web.WebSocketResponse, req: dict) -> None:
        if not isinstance(req, dict):
            return
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        logger.debug("Moonraker WS RPC: %s id=%s params=%s", method, rid, params)

        if method == "printer.objects.subscribe":
            fields = self._extract_heater_bed_fields(params)
            self._state.add_subscriber(ws, fields)
            # Initial response mirrors objects.query
            result = self._objects_query(params)
            await self._send_result(ws, rid, result)
            logger.info("Moonraker subscriber registered for heater_bed fields=%s", fields)
            return

        try:
            result = self._result(method or "", params)
        except _MethodNotFound:
            if rid is not None:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
            else:
                logger.debug("Moonraker WS: unknown notification %s", method)
            return

        if rid is not None:
            await self._send_result(ws, rid, result)

    async def _send_result(self, ws: web.WebSocketResponse, rid: Any, result: Any) -> None:
        await ws.send_json({"jsonrpc": "2.0", "id": rid, "result": result})

    # ---------- HTTP handlers ----------

    async def _http_root(self, _request: web.Request) -> web.Response:
        return web.json_response({"server": "panda-breath-fake-moonraker"})

    async def _http_catchall(self, request: web.Request) -> web.Response:
        logger.warning(
            "Moonraker HTTP catch-all hit: %s %s (no handler)",
            request.method,
            request.path_qs,
        )
        return web.json_response(
            {"error": {"code": -32601, "message": "Not implemented"}}, status=404
        )

    # OctoPrint compatibility (a few accessory firmwares use these)
    async def _http_api_version(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"server": _MOONRAKER_VERSION, "api": "0.1", "text": "OctoPrint (Moonraker fake)"}
        )

    async def _http_api_server(self, _request: web.Request) -> web.Response:
        return web.json_response({"server": _MOONRAKER_VERSION, "safemode": None})

    async def _http_api_login(self, _request: web.Request) -> web.Response:
        return web.json_response({"_is_external_client": False, "session": "fake"})

    async def _http_api_printer(self, _request: web.Request) -> web.Response:
        t = round(self._state.bed_temp, 2)
        tgt = round(self._state.bed_target, 2)
        return web.json_response(
            {
                "state": {"text": "Operational", "flags": {"operational": True}},
                "temperature": {
                    "bed": {"actual": t, "target": tgt, "offset": 0},
                    "tool0": {"actual": 0, "target": 0, "offset": 0},
                },
            }
        )

    async def _http_server_info(self, _request: web.Request) -> web.Response:
        return web.json_response({"result": self._server_info()})

    async def _http_printer_info(self, _request: web.Request) -> web.Response:
        return web.json_response({"result": self._printer_info()})

    async def _http_objects_list(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"result": {"objects": ["heater_bed", "extruder", "toolhead", "print_stats"]}}
        )

    async def _http_objects_query(self, request: web.Request) -> web.Response:
        # Moonraker style: /printer/objects/query?heater_bed=temperature,target
        objects: dict[str, list[str] | None] = {}
        for name, value in request.query.items():
            if value == "":
                objects[name] = None
            else:
                objects[name] = [v.strip() for v in value.split(",") if v.strip()]
        result = self._objects_query({"objects": objects})
        return web.json_response({"result": result})

    # ---------- Lifecycle ----------

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info("Fake Moonraker listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        logger.info("Fake Moonraker stopped")

    async def run_forever(self, shutdown: asyncio.Event) -> None:
        await self.start()
        try:
            await shutdown.wait()
        finally:
            await self.stop()


class _MethodNotFound(Exception):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(method)
