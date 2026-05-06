"""FastAPI entrypoint for the museum dashboard.

Run with:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 8080
or via the systemd unit in deploy/dashboard.service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from shared.protocol import Ack, Heartbeat, Register, node_key

from . import auth, config, db
from .wol import send_wol
from .ws import Connection, ConnectionManager, parse_agent_message


log = logging.getLogger("sssds.dashboard")
logging.basicConfig(level=os.environ.get("SSSDS_LOG", "INFO"))


# ---------------------------------------------------------------------------
# app + lifespan
# ---------------------------------------------------------------------------

CFG = config.load()
MGR = ConnectionManager()

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def _prune_loop() -> None:
    """Drop audit events older than the configured retention window."""
    while True:
        try:
            removed = db.prune_events(CFG.db_path, CFG.event_retention_days)
            if removed:
                log.info("pruned %d old events", removed)
        except Exception:
            log.exception("event prune failed")
        await asyncio.sleep(3600)  # hourly is plenty for daily rotation


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(CFG.db_path)
    pruner = asyncio.create_task(_prune_loop())
    log.info("dashboard listening on %s:%d", CFG.bind_host, CFG.bind_port)
    try:
        yield
    finally:
        pruner.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# auth dependency
# ---------------------------------------------------------------------------

def require_admin(sssds_session: str | None = Cookie(default=None)) -> None:
    if not auth.is_authenticated(CFG.session_secret, sssds_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, sssds_session: str | None = Cookie(default=None)):
    if not auth.is_authenticated(CFG.session_secret, sssds_session):
        return _redirect_to_login()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "nodes": _node_view_models()},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if not auth.verify_password(password, CFG.admin_password_hash):
        db.log_event(CFG.db_path, node=None, kind="login_failed", detail="")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Wrong password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    cookie = auth.issue_cookie(CFG.session_secret)
    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        auth.SESSION_COOKIE,
        cookie,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    db.log_event(CFG.db_path, node=None, kind="login_ok", detail="")
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# JSON / HTMX endpoints
# ---------------------------------------------------------------------------

@app.get("/api/nodes", dependencies=[Depends(require_admin)])
async def api_nodes():
    return {"nodes": _node_view_models()}


@app.get("/partials/tiles", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def partial_tiles(request: Request):
    """HTMX polling target. Re-renders just the tile grid."""
    return templates.TemplateResponse(
        "_tiles.html",
        {"request": request, "nodes": _node_view_models()},
    )


@app.post("/api/nodes/{key}/wake", dependencies=[Depends(require_admin)])
async def wake(key: str):
    row = db.get_node(CFG.db_path, key)
    if row is None:
        raise HTTPException(404, "unknown node")
    if not row["mac"]:
        raise HTTPException(400, "no MAC on file — node has never registered")
    addrs = ["255.255.255.255"]
    if CFG.wol_broadcast:
        addrs.append(CFG.wol_broadcast)
    try:
        send_wol(row["mac"], broadcast_addrs=addrs)
    except Exception as e:
        db.log_event(CFG.db_path, node=key, kind="wake_failed", detail=str(e))
        raise HTTPException(500, str(e))
    db.log_event(CFG.db_path, node=key, kind="wake", detail=f"mac={row['mac']}")
    return {"ok": True, "detail": f"magic packet sent to {row['mac']}"}


@app.post("/api/nodes/{key}/shutdown", dependencies=[Depends(require_admin)])
async def shutdown_node(key: str):
    return await _send(key, "shutdown")


@app.post("/api/nodes/{key}/restart", dependencies=[Depends(require_admin)])
async def restart_node(key: str):
    return await _send(key, "restart")


@app.post("/api/nodes/{key}/restart-video", dependencies=[Depends(require_admin)])
async def restart_video(key: str):
    return await _send(key, "restart_video")


async def _send(key: str, kind):
    if db.get_node(CFG.db_path, key) is None:
        raise HTTPException(404, "unknown node")
    ok, detail = await MGR.send_command(key, kind)
    db.log_event(
        CFG.db_path, node=key, kind=f"command:{kind}",
        detail=("ok" if ok else f"failed: {detail}"),
    )
    if not ok:
        raise HTTPException(409, detail)
    return {"ok": True, "detail": detail}


# ---------------------------------------------------------------------------
# WebSocket: agent <-> dashboard
# ---------------------------------------------------------------------------

@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    key: str | None = None
    try:
        # First message must be Register.
        first = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = parse_agent_message(first)
        if msg.get("type") != "register":
            await ws.close(code=4400, reason="expected register first")
            return
        try:
            reg = Register(**msg)
        except ValidationError as e:
            await ws.close(code=4400, reason=f"bad register: {e}")
            return
        if reg.token != CFG.agent_token:
            await ws.close(code=4401, reason="bad token")
            db.log_event(CFG.db_path, node=None, kind="auth_failed",
                         detail=f"zone={reg.zone} node={reg.node}")
            return

        key = node_key(reg.zone, reg.node)
        db.upsert_node(
            CFG.db_path,
            key=key,
            zone=reg.zone,
            node=reg.node,
            mac=reg.mac.lower(),
            hostname=reg.hostname,
            ip=reg.ip,
        )
        db.log_event(CFG.db_path, node=key, kind="connect",
                     detail=f"ip={reg.ip} mac={reg.mac}")
        await MGR.attach(Connection(ws=ws, key=key, ip=reg.ip))
        log.info("agent %s connected from %s", key, reg.ip)

        while True:
            raw = await ws.receive_text()
            try:
                msg = parse_agent_message(raw)
            except Exception as e:
                log.warning("bad frame from %s: %s", key, e)
                continue

            mtype = msg.get("type")
            if mtype == "heartbeat":
                try:
                    Heartbeat(**msg)  # validate but we store the raw JSON
                except ValidationError:
                    continue
                db.update_heartbeat(CFG.db_path, key, json.dumps(msg))
            elif mtype == "ack":
                try:
                    a = Ack(**msg)
                except ValidationError:
                    continue
                MGR.resolve_ack(a.command_id, a.ok, a.detail)
            else:
                log.debug("ignoring unexpected type=%r from %s", mtype, key)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error")
    finally:
        if key is not None:
            await MGR.detach(key, ws)
            db.log_event(CFG.db_path, node=key, kind="disconnect", detail="")
            log.info("agent %s disconnected", key)


# ---------------------------------------------------------------------------
# view models
# ---------------------------------------------------------------------------

def _node_view_models() -> list[dict]:
    nodes = db.list_nodes(CFG.db_path)
    online = MGR.online_keys()
    now = int(time.time())
    out = []
    for n in nodes:
        last_seen = n["last_seen"] or 0
        connected = n["key"] in online
        # "stale" = registered but heartbeat is older than offline_after
        stale = (not connected) and (now - last_seen > CFG.offline_after_seconds)
        if connected:
            state = "online"
        elif last_seen == 0:
            state = "offline"
        elif stale:
            state = "offline"
        else:
            state = "stale"
        out.append({
            "key": n["key"],
            "zone": n["zone"],
            "node": n["node"],
            "ip": n["last_ip"] or "",
            "mac": n["mac"] or "",
            "hostname": n["hostname"] or "",
            "last_seen": last_seen,
            "last_seen_human": _humanize_age(now - last_seen) if last_seen else "never",
            "state": state,
        })
    return out


def _humanize_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"
