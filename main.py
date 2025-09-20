import os, json, asyncio, time, math
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Body
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

MEDIA_DIR = "media"
DB_FILE = "db.json"
Q_FILE = "queue.json"

os.makedirs(MEDIA_DIR, exist_ok=True)
if not os.path.exists(DB_FILE): open(DB_FILE, "w").write("{}")
if not os.path.exists(Q_FILE): open(Q_FILE, "w").write("[]")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def mmss(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s//60}:{s%60:02d}"

# ---- DB y Cola (bloqueo ligero) ----
_db_lock = asyncio.Lock()
_q_lock = asyncio.Lock()

def db_read() -> Dict[str, Any]: return load_json(DB_FILE, {})
def db_write(db: Dict[str, Any]): save_json(DB_FILE, db)
def q_read() -> List[str]: return load_json(Q_FILE, [])
def q_write(q: List[str]): save_json(Q_FILE, q)

# ---- Descarga / Registro con yt-dlp ----
def _download_yt_to_mp3(url_or_id: str) -> Dict[str, Any]:
    # Si ya tenemos metadata + archivo, no descargamos
    db = db_read()
    if url_or_id in db:
        fn = os.path.join(MEDIA_DIR, db[url_or_id]["filename"])
        if os.path.exists(fn): return db[url_or_id]

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(MEDIA_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        # Usa ffmpeg del sistema (asegúrate de tenerlo en PATH)
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url_or_id, download=True)
        vid = info.get("id")
        title = info.get("title")
        duration = int(info.get("duration") or 0)
        filename = f"{vid}.mp3"
        rec = {"id": vid, "title": title, "seconds": duration, "filename": filename}
        db = db_read(); db[vid] = rec; db_write(db)
        return rec

async def ensure_track(url_or_id: str) -> Dict[str, Any]:
    # Ejecuta descarga en hilo para no bloquear el loop
    return await asyncio.to_thread(_download_yt_to_mp3, url_or_id)

# ---- Streaming con Range ----
CHUNK = 1024 * 1024
def ranged_stream(file_path: str, start: int, end: int):
    with open(file_path, "rb") as f:
        f.seek(start)
        bytes_left = end - start + 1
        while bytes_left > 0:
            chunk = f.read(min(CHUNK, bytes_left))
            if not chunk: break
            bytes_left -= len(chunk)
            yield chunk

# ---- Estado del Player (servidor autoritativo) ----
class PlayerState:
    def __init__(self):
        self.current_id: Optional[str] = None
        self.duration: float = 0.0
        self.started_at: Optional[float] = None  # epoch seconds cuando comenzó
        self.paused: bool = False
        self.paused_at: float = 0.0

    def playing(self) -> bool:
        return self.current_id is not None and not self.paused

    def pos(self) -> float:
        if not self.current_id: return 0.0
        if self.paused: return self.paused_at
        if self.started_at is None: return 0.0
        return max(0.0, time.time() - self.started_at)

player = PlayerState()
state_lock = asyncio.Lock()

# ---- WebSocket Manager ----
class WSManager:
    def __init__(self): self.active: set[WebSocket] = set()
    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active.add(ws)
    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
    async def broadcast(self, payload: Dict[str, Any]):
        dead = []
        for ws in list(self.active):
            try: await ws.send_json(payload)
            except Exception: dead.append(ws)
        for ws in dead: self.disconnect(ws)

ws_manager = WSManager()

# ---- Helpers de cola / reproducción ----
async def up_next_ids() -> List[str]:
    async with _q_lock: return q_read()

async def up_next_detailed() -> List[Dict[str, Any]]:
    ids = await up_next_ids()
    db = db_read()
    return [db[i] for i in ids if i in db]

async def enqueue_track(track_id: str):
    async with _q_lock:
        q = q_read(); q.append(track_id); q_write(q)
    await broadcast_queue(); await maybe_autostart()

async def shift_queue() -> Optional[str]:
    async with _q_lock:
        q = q_read()
        if not q: return None
        id_ = q.pop(0); q_write(q)
    await broadcast_queue()
    return id_

def public_state() -> Dict[str, Any]:
    db = db_read()
    cur = db.get(player.current_id) if player.current_id else None
    pos = player.pos()
    return {
        "playing": player.playing(),
        "position": pos,
        "positionLabel": mmss(pos),
        "duration": (cur or {}).get("seconds", 0),
        "durationLabel": mmss((cur or {}).get("seconds", 0)),
        "current": {"id": cur["id"], "title": cur["title"]} if cur else None,
    }

async def broadcast_state():
    await ws_manager.broadcast({"type": "state", "data": {
        **public_state(),
        "queue": await up_next_detailed()
    }})

async def broadcast_queue():
    await ws_manager.broadcast({"type": "queue:update", "data": await up_next_detailed()})

async def maybe_autostart():
    async with state_lock:
        if player.current_id: return
        next_id = await shift_queue()
        if not next_id:
            # nada que reproducir
            await broadcast_state()
            return
        db = db_read(); meta = db.get(next_id)
        if not meta:  # si falta metadata, intenta siguiente
            await maybe_autostart(); return
        player.current_id = next_id
        player.duration = float(meta.get("seconds") or 0)
        player.paused = False
        player.paused_at = 0.0
        player.started_at = time.time()
    await ws_manager.broadcast({"type":"player:next","data":{"current":{"id":meta["id"],"title":meta["title"]}}})
    await broadcast_state()

async def cmd_play(at: Optional[float] = None):
    async with state_lock:
        if not player.current_id: 
            await maybe_autostart(); return
        if not player.paused: return
        seek = player.paused_at if at is None else max(0.0, float(at))
        player.started_at = time.time() - seek
        player.paused = False
    await broadcast_state()

async def cmd_pause():
    async with state_lock:
        if not player.current_id or player.paused: return
        player.paused_at = player.pos()
        player.paused = True
    await broadcast_state()

async def cmd_seek(at: float):
    async with state_lock:
        if not player.current_id: return
        at = max(0.0, min(float(at), player.duration or 0.0))
        if player.paused: player.paused_at = at
        else: player.started_at = time.time() - at
    await broadcast_state()

async def cmd_next():
    # Detener actual y pasar al siguiente
    async with state_lock:
        player.current_id = None
        player.duration = 0.0
        player.started_at = None
        player.paused = False
        player.paused_at = 0.0
    await maybe_autostart()

# ---- Ticker: emite tiempo y avanza al terminar ----
async def ticker():
    while True:
        await asyncio.sleep(1)
        if not player.current_id or player.paused: 
            continue
        pos = player.pos()
        await ws_manager.broadcast({"type":"player:tick","data":{"position":pos,"positionLabel":mmss(pos)}})
        if player.duration and pos >= player.duration - 0.3:
            await cmd_next()

app = FastAPI(title="Music Realtime FastAPI")

@app.on_event("startup")
async def _startup():
    asyncio.create_task(ticker())

# -------- REST ----------
class TrackIn(BaseModel):
    urlOrId: str
    enqueue: Optional[bool] = False

@app.get("/api/tracks")
def api_tracks():
    return list(db_read().values())

@app.post("/api/tracks")
async def api_add_track(payload: TrackIn):
    rec = await ensure_track(payload.urlOrId)
    if payload.enqueue:
        await enqueue_track(rec["id"])
    return rec

@app.get("/api/queue")
async def api_queue():
    return await up_next_detailed()

@app.post("/api/queue/next")
async def api_queue_next():
    await cmd_next()
    return {"ok": True}

# Streaming con soporte de Range
@app.get("/stream/{track_id}")
def stream(request: Request, track_id: str):
    file_path = os.path.join(MEDIA_DIR, f"{track_id}.mp3")
    if not os.path.exists(file_path):
        return JSONResponse({"error": "not found"}, status_code=404)
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")
    if range_header is None:
        # sin Range → stream completo
        def full():
            with open(file_path, "rb") as f:
                while True:
                    data = f.read(CHUNK)
                    if not data: break
                    yield data
        return StreamingResponse(full(), media_type="audio/mpeg")
    # con Range
    bytes_range = range_header.replace("bytes=", "").split("-")
    start = int(bytes_range[0]) if bytes_range[0] else 0
    end = int(bytes_range[1]) if len(bytes_range) > 1 and bytes_range[1] else file_size - 1
    start = max(0, start); end = min(end, file_size - 1)
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Type": "audio/mpeg",
    }
    return StreamingResponse(ranged_stream(file_path, start, end), status_code=206, headers=headers)

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("index.html")

# -------- WebSocket --------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # Estado inicial
        await ws.send_json({"type":"state","data":{**public_state(),"queue": await up_next_detailed()}})
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "queue:add":
                url_or_id = msg.get("urlOrId") or msg.get("id")
                if not url_or_id: 
                    await ws.send_json({"type":"error","data":{"message":"urlOrId requerido"}}); 
                    continue
                try:
                    rec = await ensure_track(url_or_id)
                    await enqueue_track(rec["id"])
                    await ws.send_json({"type":"ok","data":{"action":"queue:add","id":rec["id"]}})
                except Exception as e:
                    await ws.send_json({"type":"error","data":{"message":str(e)}})
            elif t == "player:play":
                await cmd_play(msg.get("at"))
            elif t == "player:pause":
                await cmd_pause()
            elif t == "player:seek":
                await cmd_seek(float(msg.get("at") or 0))
            elif t == "player:next":
                await cmd_next()
            elif t == "state:get":
                await ws.send_json({"type":"state","data":{**public_state(),"queue": await up_next_detailed()}})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
