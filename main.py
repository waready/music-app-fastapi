import os, json, asyncio, time, math, random
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Body
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
# Reemplazado yt-dlp con pytubefix para evitar bot detection
# from yt_dlp import YoutubeDL
from pytubefix import YouTube, Search
from pytubefix.exceptions import VideoUnavailable, PytubeFixError
import bcrypt

# Modo de streaming: "direct" (desde YouTube) o "download" (descargar MP3)
STREAMING_MODE = os.getenv("STREAMING_MODE", "direct")
MEDIA_DIR = "media"
DB_FILE = "db.json"
ROOMS_FILE = "rooms.json"
USERS_FILE = "users.json"
SESSIONS_FILE = "sessions.json"

os.makedirs(MEDIA_DIR, exist_ok=True)
if not os.path.exists(DB_FILE): open(DB_FILE, "w").write("{}")
if not os.path.exists(ROOMS_FILE): open(ROOMS_FILE, "w").write("{}")
if not os.path.exists(USERS_FILE): open(USERS_FILE, "w").write("{}")
if not os.path.exists(SESSIONS_FILE): open(SESSIONS_FILE, "w").write("{}")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def mmss(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s//60}:{s%60:02d}"

# ---- DB y Locks ----
_db_lock = asyncio.Lock()
_rooms_lock = asyncio.Lock()
_users_lock = asyncio.Lock()
_sessions_lock = asyncio.Lock()

def db_read() -> Dict[str, Any]: return load_json(DB_FILE, {})
def db_write(db: Dict[str, Any]): save_json(DB_FILE, db)

def rooms_read() -> Dict[str, Any]: return load_json(ROOMS_FILE, {})
def rooms_write(rooms: Dict[str, Any]): save_json(ROOMS_FILE, rooms)

def users_read() -> Dict[str, Any]: return load_json(USERS_FILE, {})
def users_write(users: Dict[str, Any]): save_json(USERS_FILE, users)

def sessions_read() -> Dict[str, Any]: return load_json(SESSIONS_FILE, {})
def sessions_write(sessions: Dict[str, Any]): save_json(SESSIONS_FILE, sessions)

# ---- Anti-bot helpers ----
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

last_request_time = 0
bot_detection_count = 0
last_bot_detection = 0

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def is_bot_detection_active():
    """Verifica si estamos en un período de enfriamiento por detección de bot"""
    global bot_detection_count, last_bot_detection
    current_time = time.time()

    # Si hemos tenido muchas detecciones recientes, pausar
    if bot_detection_count >= 3 and current_time - last_bot_detection < 3600:  # 1 hora
        return True

    # Reset counter después de 2 horas sin detecciones
    if current_time - last_bot_detection > 7200:  # 2 horas
        bot_detection_count = 0

    return False

def report_bot_detection():
    """Reporta una detección de bot y actualiza contadores"""
    global bot_detection_count, last_bot_detection
    bot_detection_count += 1
    last_bot_detection = time.time()
    print(f"[BOT-DETECTION] Conteo: {bot_detection_count}, pausando solicitudes...")

async def adaptive_delay():
    """Implementa un delay adaptativo entre solicitudes"""
    global last_request_time

    # Verificar si estamos en período de enfriamiento
    if is_bot_detection_active():
        print("[BOT-DETECTION] En período de enfriamiento, rechazando solicitud")
        raise Exception("Servicio temporalmente no disponible debido a limitaciones de YouTube")

    current_time = time.time()
    time_since_last = current_time - last_request_time

    # Delay más largo si hemos tenido detecciones recientes
    base_delay = 5 if bot_detection_count > 0 else 2
    min_delay = random.uniform(base_delay, base_delay + 3)

    if time_since_last < min_delay:
        delay = min_delay - time_since_last
        print(f"[DELAY] Esperando {delay:.2f}s antes de la siguiente solicitud")
        await asyncio.sleep(delay)

    last_request_time = time.time()

def _refresh_track_background(url_or_id: str):
    """Refresca un track en background sin bloquear"""
    try:
        _get_yt_stream_info_pytubefix(url_or_id)
        print(f"[BACKGROUND] Refresh exitoso para {url_or_id}")
    except Exception as e:
        print(f"[BACKGROUND] Error refrescando {url_or_id}: {e}")

# ---- Búsqueda en YouTube ----
def _search_youtube_deprecated(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """Buscar videos en YouTube usando yt-dlp - DEPRECATED: usar pytubefix"""
    print("[DEPRECATED] _search_youtube_deprecated called - use pytubefix instead")
    return []

async def search_youtube_async(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """Ejecuta búsqueda en hilo para no bloquear el loop - DEPRECATED"""
    return await asyncio.to_thread(_search_youtube_deprecated, query, max_results)

def _search_youtube_pytubefix(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """Buscar videos en YouTube usando pytubefix (reemplazo de yt-dlp)"""
    try:
        print(f"[PYTUBEFIX] Buscando: {query} (max: {max_results})")
        s = Search(query)

        formatted_results = []
        count = 0

        for video in s.results:
            if count >= max_results:
                break

            try:
                # Obtener duración (puede requerir acceso adicional al video)
                duration = 0
                try:
                    duration = video.length or 0
                except:
                    duration = 0

                formatted_results.append({
                    'id': {
                        'kind': 'youtube#video',
                        'videoId': video.video_id
                    },
                    'snippet': {
                        'title': video.title,
                        'description': '',  # pytubefix no proporciona descripción en búsqueda
                        'thumbnails': {
                            'default': {
                                'url': f"https://i.ytimg.com/vi/{video.video_id}/hqdefault.jpg"
                            }
                        },
                        'channelTitle': video.author or 'Unknown',
                    },
                    'duration': duration,
                    'url': video.watch_url
                })
                count += 1
            except Exception as e:
                print(f"[PYTUBEFIX] Error procesando video {video.video_id}: {e}")
                continue

        print(f"[PYTUBEFIX] Encontrados {len(formatted_results)} resultados para '{query}'")
        return formatted_results

    except Exception as e:
        print(f"[PYTUBEFIX] Error buscando en YouTube: {e}")
        return []

async def search_youtube_pytubefix_async(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """Ejecuta búsqueda pytubefix en hilo para no bloquear el loop"""
    return await asyncio.to_thread(_search_youtube_pytubefix, query, max_results)

# ---- Streaming Directo / Descarga con yt-dlp ----
def _try_alternative_extractor_deprecated(url_or_id: str) -> Dict[str, Any]:
    """Intenta usar extractores alternativos cuando YouTube falla - DEPRECATED"""
    try:
        # Intento con configuración mínima usando solo metadatos básicos
        ydl_opts_minimal = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "format": "worst",
            "user_agent": get_random_user_agent(),
        }

        # DEPRECATED: Función reemplazada por pytubefix
        return None
        # DEPRECATED: Código comentado - usar pytubefix

    except Exception as e:
        print(f"[ALTERNATIVE] También falló extractor alternativo: {e}")

    return None

def _get_yt_stream_info_deprecated(url_or_id: str) -> Dict[str, Any]:
    """Extrae metadata + URL de streaming directo SIN descargar (modo 'direct') - DEPRECATED"""
    print("[DEPRECATED] _get_yt_stream_info_deprecated called - use pytubefix instead")
    raise Exception("yt-dlp deprecated, use pytubefix instead")

def _get_yt_stream_info_pytubefix(url_or_id: str) -> Dict[str, Any]:
    """Extrae metadata + URL de streaming usando pytubefix (reemplazo de yt-dlp)"""
    db = db_read()

    # Si ya existe, verificar si podemos usarlo
    if url_or_id in db:
        cached = db[url_or_id]
        timestamp = cached.get("timestamp", 0)
        mode = cached.get("mode", "pytubefix")

        # Para tracks no disponibles, mantener el cache por 2 horas antes de reintentar
        if mode == "unavailable":
            if time.time() - timestamp < 7200:  # 2 horas
                return cached
            else:
                print(f"[CACHE] Reintentando track previamente no disponible: {url_or_id}")

        # Para tracks exitosos, URLs expiran en ~6 horas, refrescamos a las 5h
        elif time.time() - timestamp < 18000:  # 5 horas = 18000 seg
            return cached

    # Construir URL completa si solo tenemos ID
    video_url = url_or_id
    if not url_or_id.startswith("http"):
        video_url = f"https://www.youtube.com/watch?v={url_or_id}"

    try:
        print(f"[PYTUBEFIX] Extrayendo información para {url_or_id}")
        yt = YouTube(video_url)

        # Obtener metadata básica
        vid = yt.video_id
        title = yt.title
        duration = yt.length or 0

        # Obtener stream de audio de mejor calidad
        audio_stream = yt.streams.get_audio_only()
        if not audio_stream:
            # Fallback: intentar con cualquier stream de audio
            audio_stream = yt.streams.filter(only_audio=True).first()

        if not audio_stream:
            raise Exception("No se encontró stream de audio disponible")

        # Obtener URL del stream
        stream_url = audio_stream.url

        rec = {
            "id": vid,
            "title": title,
            "seconds": duration,
            "stream_url": stream_url,
            "timestamp": time.time(),
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "mode": "pytubefix"
        }

        # Guardar en DB
        db = db_read()
        db[vid] = rec
        db_write(db)
        print(f"[PYTUBEFIX] Éxito extrayendo {title} ({duration}s)")
        return rec

    except VideoUnavailable as e:
        error_msg = f"Video no disponible: {e}"
        print(f"[PYTUBEFIX] {error_msg}")
    except PytubeFixError as e:
        error_msg = f"Error de PyTubeFix: {e}"
        print(f"[PYTUBEFIX] {error_msg}")
    except Exception as e:
        error_msg = f"Error inesperado: {e}"
        print(f"[PYTUBEFIX] {error_msg}")

    # Marcar como no disponible en la DB
    rec = {
        "id": url_or_id,
        "title": f"Video {url_or_id} (No disponible)",
        "seconds": 0,
        "timestamp": time.time(),
        "error": error_msg,
        "mode": "unavailable"
    }
    db = db_read()
    db[url_or_id] = rec
    db_write(db)
    raise Exception(error_msg)

def _download_yt_to_mp3(url_or_id: str) -> Dict[str, Any]:
    """Descarga MP3 a disco (modo 'download')"""
    # Si ya tenemos metadata + archivo, no descargamos
    db = db_read()
    if url_or_id in db:
        fn = os.path.join(MEDIA_DIR, db[url_or_id].get("filename", f"{url_or_id}.mp3"))
        if os.path.exists(fn): return db[url_or_id]

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(MEDIA_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    }
    # DEPRECATED: Reemplazado por pytubefix - modo download no implementado aún
    raise Exception("Modo download no implementado con pytubefix")
    # DEPRECATED: Código comentado - usar pytubefix

async def ensure_track(url_or_id: str) -> Dict[str, Any]:
    """Obtiene track según el modo configurado"""
    # Aplicar delay adaptativo antes de procesar
    await adaptive_delay()

    db = db_read()

    # Si ya existe en DB, retornar inmediatamente
    if url_or_id in db:
        cached = db[url_or_id]
        # Para modo direct, verificar si URL expiró
        if STREAMING_MODE == "direct":
            timestamp = cached.get("timestamp", 0)
            if time.time() - timestamp < 18000:  # 5 horas
                return cached
            # Si expiró, refrescar en background pero devolver el cache actual
            print(f"[CACHE] URL expirada para {url_or_id}, refrescando en background")
            asyncio.create_task(asyncio.to_thread(_refresh_track_background, url_or_id))
        return cached

    # Extraer video ID de URL si es necesario
    video_id = url_or_id
    if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
        # Extraer ID de la URL
        if "v=" in url_or_id:
            video_id = url_or_id.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url_or_id:
            video_id = url_or_id.split("youtu.be/")[1].split("?")[0]

    # Crear entrada temporal con estado "processing"
    temp_record = {
        "id": video_id,
        "title": "Procesando...",
        "seconds": 0,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "mode": STREAMING_MODE,
        "processing": True,  # Indica que está en proceso
    }

    # Guardar registro temporal en DB
    db[video_id] = temp_record
    db_write(db)

    # Procesar en background sin bloquear
    if STREAMING_MODE == "direct":
        asyncio.create_task(asyncio.to_thread(_get_yt_stream_info_pytubefix, url_or_id))
    else:
        asyncio.create_task(asyncio.to_thread(_download_yt_to_mp3, url_or_id))

    return temp_record

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

# ---- Room System ----
class Room:
    def __init__(self, room_id: str, name: str = "", created_by: Optional[str] = None,
                 is_public: bool = True, password_hash: Optional[str] = None):
        self.id = room_id
        self.name = name or f"Room {room_id}"
        self.created_by = created_by  # user_id del creador
        self.is_public = is_public  # True = pública, False = privada
        self.password_hash = password_hash  # Hash bcrypt de la contraseña (solo para privadas)
        self.queue: List[str] = []
        self.player = PlayerState()
        self.connections: set[WebSocket] = set()
        self.state_lock = asyncio.Lock()
        self.queue_lock = asyncio.Lock()

    async def get_queue_detailed(self) -> List[Dict[str, Any]]:
        async with self.queue_lock:
            db = db_read()
            return [db[i] for i in self.queue if i in db]

    def public_state(self) -> Dict[str, Any]:
        db = db_read()
        cur = db.get(self.player.current_id) if self.player.current_id else None
        pos = self.player.pos()
        return {
            "playing": self.player.playing(),
            "position": pos,
            "positionLabel": mmss(pos),
            "duration": (cur or {}).get("seconds", 0),
            "durationLabel": mmss((cur or {}).get("seconds", 0)),
            "current": {"id": cur["id"], "title": cur["title"]} if cur else None,
        }

    async def broadcast(self, payload: Dict[str, Any]):
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.discard(ws)

# Rooms activas en memoria
rooms: Dict[str, Room] = {}
rooms_lock = asyncio.Lock()

async def get_or_create_room(room_id: str, user_id: Optional[str] = None,
                             is_public: bool = True, password_hash: Optional[str] = None) -> Room:
    """Obtiene o crea una room"""
    async with rooms_lock:
        if room_id not in rooms:
            # Intentar cargar de disco
            all_rooms = rooms_read()
            if room_id in all_rooms:
                room_data = all_rooms[room_id]
                room = Room(
                    room_id,
                    room_data.get("name", ""),
                    room_data.get("created_by"),
                    room_data.get("is_public", True),
                    room_data.get("password_hash")
                )
                room.queue = room_data.get("queue", [])
            else:
                # Crear nueva room
                room = Room(room_id, created_by=user_id, is_public=is_public, password_hash=password_hash)
                # Guardar en disco
                all_rooms[room_id] = {
                    "id": room_id,
                    "name": room.name,
                    "created_by": user_id,
                    "is_public": is_public,
                    "password_hash": password_hash,
                    "queue": []
                }
                rooms_write(all_rooms)
            rooms[room_id] = room
        return rooms[room_id]

async def save_room_state(room: Room):
    """Guarda el estado de una room a disco"""
    async with _rooms_lock:
        all_rooms = rooms_read()
        all_rooms[room.id] = {
            "id": room.id,
            "name": room.name,
            "created_by": room.created_by,
            "is_public": room.is_public,
            "password_hash": room.password_hash,
            "queue": room.queue
        }
        rooms_write(all_rooms)

# ---- Helpers de cola / reproducción por Room ----
async def room_enqueue_track(room: Room, track_id: str):
    async with room.queue_lock:
        # Evitar duplicados en la cola
        if track_id not in room.queue:
            room.queue.append(track_id)
    await save_room_state(room)
    await room_broadcast_queue(room)
    await room_maybe_autostart(room)

async def room_shift_queue(room: Room) -> Optional[str]:
    async with room.queue_lock:
        if not room.queue: return None
        id_ = room.queue.pop(0)
    await save_room_state(room)
    await room_broadcast_queue(room)
    return id_

async def room_broadcast_state(room: Room):
    await room.broadcast({"type": "state", "data": {
        **room.public_state(),
        "queue": await room.get_queue_detailed()
    }})

async def room_broadcast_queue(room: Room):
    await room.broadcast({"type": "queue:update", "data": await room.get_queue_detailed()})

async def room_maybe_autostart(room: Room):
    async with room.state_lock:
        print(f"[DEBUG] room_maybe_autostart for room {room.id}: current_id={room.player.current_id}, queue_len={len(room.queue)}")
        if room.player.current_id:
            print(f"[DEBUG] Already playing {room.player.current_id}, skipping autostart")
            return
        next_id = await room_shift_queue(room)
        print(f"[DEBUG] Shifted next_id: {next_id}")
        if not next_id:
            print(f"[DEBUG] No next track in queue, broadcasting empty state")
            await room_broadcast_state(room)
            return
        db = db_read(); meta = db.get(next_id)
        if not meta:
            print(f"[DEBUG] Track {next_id} not found in DB, recursing")
            await room_maybe_autostart(room); return
        print(f"[DEBUG] Starting track {next_id}: {meta.get('title')}")
        room.player.current_id = next_id

        # Verificar si es modo híbrido
        if meta.get("mode") == "hybrid":
            print(f"[HYBRID] Track {next_id} is in hybrid mode, using client-side player")
            # Para modo híbrido, usar duración placeholder - el cliente actualizará la duración real
            room.player.duration = 300.0  # 5 minutos placeholder
        else:
            # Modo tradicional con yt-dlp
            room.player.duration = float(meta.get("seconds") or 0)

        room.player.paused = False
        room.player.paused_at = 0.0
        room.player.started_at = time.time()
        print(f"[DEBUG] Player state: current_id={room.player.current_id}, paused={room.player.paused}, playing={room.player.playing()}, mode={meta.get('mode', 'traditional')}")
    await room.broadcast({"type":"player:next","data":{"current":{"id":meta["id"],"title":meta["title"]}}})
    await room_broadcast_state(room)

async def room_cmd_play(room: Room, at: Optional[float] = None):
    async with room.state_lock:
        if not room.player.current_id:
            await room_maybe_autostart(room); return
        if not room.player.paused: return
        seek = room.player.paused_at if at is None else max(0.0, float(at))
        room.player.started_at = time.time() - seek
        room.player.paused = False
    await room_broadcast_state(room)

async def room_cmd_pause(room: Room):
    async with room.state_lock:
        if not room.player.current_id or room.player.paused: return
        room.player.paused_at = room.player.pos()
        room.player.paused = True
    await room_broadcast_state(room)

async def room_cmd_seek(room: Room, at: float):
    async with room.state_lock:
        if not room.player.current_id: return
        at = max(0.0, min(float(at), room.player.duration or 0.0))
        if room.player.paused: room.player.paused_at = at
        else: room.player.started_at = time.time() - at
    await room_broadcast_state(room)

async def room_cmd_next(room: Room):
    async with room.state_lock:
        room.player.current_id = None
        room.player.duration = 0.0
        room.player.started_at = None
        room.player.paused = False
        room.player.paused_at = 0.0
    await room_maybe_autostart(room)

# ---- Ticker: emite tiempo y avanza al terminar (para todas las rooms) ----
async def ticker():
    while True:
        await asyncio.sleep(1)
        for room_id, room in list(rooms.items()):
            if not room.player.current_id or room.player.paused:
                continue
            pos = room.player.pos()
            await room.broadcast({"type":"player:tick","data":{"position":pos,"positionLabel":mmss(pos)}})
            if room.player.duration and pos >= room.player.duration - 0.3:
                await room_cmd_next(room)

app = FastAPI(title="Music Realtime FastAPI")

# Configurar CORS para permitir acceso desde GitHub Pages u otros orígenes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, cambiar a tu dominio de GitHub Pages
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def cleanup_old_sessions():
    """Limpia sesiones antiguas cada hora"""
    while True:
        await asyncio.sleep(3600)  # 1 hora
        try:
            async with _sessions_lock:
                sessions = sessions_read()
                current_time = time.time()
                # Eliminar sesiones mayores a 24 horas
                old_tokens = [
                    token for token, data in sessions.items()
                    if current_time - data.get("created_at", 0) > 86400
                ]
                for token in old_tokens:
                    del sessions[token]
                if old_tokens:
                    sessions_write(sessions)
                    print(f"[CLEANUP] Eliminadas {len(old_tokens)} sesiones antiguas")
        except Exception as e:
            print(f"[CLEANUP] Error: {e}")

@app.on_event("startup")
async def _startup():
    asyncio.create_task(ticker())
    asyncio.create_task(cleanup_old_sessions())

# -------- REST ----------
@app.get("/health")
def health_check():
    """Health check endpoint para UptimeRobot y Render con estadísticas"""
    from datetime import datetime

    # Estadísticas básicas
    db = db_read()
    total_tracks = len(db)
    unavailable_tracks = len([t for t in db.values() if t.get("mode") == "unavailable"])
    success_rate = ((total_tracks - unavailable_tracks) / total_tracks * 100) if total_tracks > 0 else 100

    # Estado del sistema de detección de bot
    cooldown_active = is_bot_detection_active()

    status = "healthy"
    if success_rate < 50:
        status = "degraded"
    if cooldown_active:
        status = "limited"

    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "stats": {
            "total_tracks": total_tracks,
            "unavailable_tracks": unavailable_tracks,
            "success_rate": round(success_rate, 1),
            "bot_detection_count": bot_detection_count,
            "cooldown_active": cooldown_active
        }
    }

class TrackIn(BaseModel):
    urlOrId: str
    enqueue: Optional[bool] = False

@app.get("/api/tracks")
def api_tracks():
    return list(db_read().values())

@app.post("/api/admin/clear-cache")
def api_clear_cache(track_ids: List[str] = Body(default=[])):
    """Limpia el cache de tracks específicos o todos si no se especifican"""
    db = db_read()

    if not track_ids:
        # Limpiar solo tracks no disponibles
        cleared = 0
        for track_id in list(db.keys()):
            if db[track_id].get("mode") == "unavailable":
                del db[track_id]
                cleared += 1
        db_write(db)
        return {"message": f"Cache limpiado: {cleared} tracks no disponibles eliminados"}
    else:
        # Limpiar tracks específicos
        cleared = 0
        for track_id in track_ids:
            if track_id in db:
                del db[track_id]
                cleared += 1
        db_write(db)
        return {"message": f"Cache limpiado: {cleared} tracks eliminados"}

@app.post("/api/admin/reset-bot-detection")
def api_reset_bot_detection():
    """Resetea el contador de detección de bot manualmente"""
    global bot_detection_count, last_bot_detection
    old_count = bot_detection_count
    bot_detection_count = 0
    last_bot_detection = 0
    return {"message": f"Contador de detección de bot reseteado (era: {old_count})"}

@app.post("/api/tracks")
async def api_add_track(payload: TrackIn):
    rec = await ensure_track(payload.urlOrId)
    if payload.enqueue:
        # Legacy: usar room "default"
        room = await get_or_create_room("default")
        await room_enqueue_track(room, rec["id"])
    return rec

@app.get("/api/queue")
async def api_queue():
    # Legacy endpoint: retorna cola de room "default"
    room = await get_or_create_room("default")
    return await room.get_queue_detailed()

@app.post("/api/queue/next")
async def api_queue_next():
    # Legacy endpoint: avanza en room "default"
    room = await get_or_create_room("default")
    await room_cmd_next(room)
    return {"ok": True}

@app.post("/api/download")
async def api_download_track(payload: TrackIn):
    """Descarga una canción sin agregarla a la cola principal (para playlists)"""
    rec = await ensure_track(payload.urlOrId)
    return rec

@app.get("/api/search")
async def api_search(q: str, maxResults: int = 30):
    """Buscar música en YouTube"""
    if not q:
        return JSONResponse({"error": "Query parameter 'q' is required"}, status_code=400)

    results = await search_youtube_pytubefix_async(q, maxResults)
    return {
        "items": results,
        "pageInfo": {
            "totalResults": len(results),
            "resultsPerPage": maxResults
        }
    }

# Streaming: modo directo o archivo local
@app.api_route("/stream/{track_id}", methods=["GET", "HEAD"])
async def stream(request: Request, track_id: str):
    db = db_read()

    if track_id not in db:
        return JSONResponse({"error": "Track no encontrado"}, status_code=404)

    track = db[track_id]
    mode = track.get("mode", STREAMING_MODE)

    # MODO HÍBRIDO: No procesar con servidor, usar cliente
    if mode == "hybrid":
        return JSONResponse({
            "error": "Track en modo híbrido",
            "message": "Esta canción está siendo reproducida directamente por el cliente (YouTube Player)",
            "track_id": track_id,
            "title": track.get("title", "YouTube Video"),
            "mode": "hybrid"
        }, status_code=503)

    # MODO NO DISPONIBLE: YouTube está bloqueando el acceso
    if mode == "unavailable":
        error_msg = track.get("error", "Track no disponible temporalmente")
        return JSONResponse({
            "error": error_msg,
            "message": "YouTube está bloqueando el acceso desde este servidor. Intenta más tarde.",
            "track_id": track_id,
            "title": track.get("title", "Unknown")
        }, status_code=503)

    # MODO DIRECTO: Proxy streaming desde YouTube (evita CORS)
    if mode == "direct":
        stream_url = track.get("stream_url")
        timestamp = track.get("timestamp", 0)

        # Si la URL expiró (> 5 horas), refrescarla
        if not stream_url or time.time() - timestamp > 18000:
            try:
                print(f"Refrescando URL expirada para {track_id}")
                await adaptive_delay()  # Aplicar delay antes de refrescar
                track = await asyncio.to_thread(_get_yt_stream_info, track_id)
                stream_url = track.get("stream_url")
            except Exception as e:
                print(f"Error refrescando stream: {e}")
                # Si falla, intentar usar la URL expirada por si aún funciona
                if stream_url:
                    print(f"Intentando con URL posiblemente expirada...")
                else:
                    return JSONResponse({"error": "No se pudo obtener stream de YouTube. Intenta más tarde."}, status_code=503)

        # Proxy con soporte completo para Range requests
        import httpx

        try:
            # Preparar headers para enviar a YouTube
            headers_to_youtube = {}

            # Pasar Range header si existe (para seeking en el navegador)
            range_header = request.headers.get("range")
            if range_header:
                headers_to_youtube["Range"] = range_header
                print(f"[PROXY] Range request: {range_header}")

            # Configurar timeout largo para streaming
            timeout = httpx.Timeout(60.0, connect=10.0)

            # Hacer request a YouTube con streaming
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                youtube_response = await client.get(stream_url, headers=headers_to_youtube)
                youtube_response.raise_for_status()

                # Preparar headers de respuesta
                response_headers = {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
                    "Accept-Ranges": "bytes",
                }

                # Pasar Content-Length si existe
                if "content-length" in youtube_response.headers:
                    response_headers["Content-Length"] = youtube_response.headers["content-length"]

                # Pasar Content-Range si existe (para respuestas 206)
                if "content-range" in youtube_response.headers:
                    response_headers["Content-Range"] = youtube_response.headers["content-range"]

                # Pasar Content-Type
                content_type = youtube_response.headers.get("content-type", "audio/webm")

                # Determinar status code (200 para full content, 206 para partial)
                status_code = youtube_response.status_code

                print(f"[PROXY] YouTube response: {status_code}, Content-Length: {response_headers.get('Content-Length', 'unknown')}")

                # Crear generador para streaming
                async def stream_youtube_content():
                    async for chunk in youtube_response.aiter_bytes(chunk_size=65536):
                        yield chunk

                return StreamingResponse(
                    stream_youtube_content(),
                    status_code=status_code,
                    media_type=content_type,
                    headers=response_headers
                )

        except httpx.HTTPStatusError as e:
            print(f"[PROXY] HTTP error: {e.response.status_code}")
            return JSONResponse({"error": f"YouTube error: {e.response.status_code}"}, status_code=502)
        except httpx.TimeoutException:
            print(f"[PROXY] Timeout streaming from YouTube")
            return JSONResponse({"error": "Timeout streaming from YouTube"}, status_code=504)
        except Exception as e:
            print(f"[PROXY] Error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse({"error": str(e)}, status_code=500)

    # MODO DOWNLOAD: Servir archivo local con Range support
    else:
        file_path = os.path.join(MEDIA_DIR, track.get("filename", f"{track_id}.mp3"))
        if not os.path.exists(file_path):
            return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)

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

@app.get("/ready/{video_id}")
def check_ready(video_id: str):
    """Verifica si un video está descargado y listo para reproducir"""
    file_path = os.path.join(MEDIA_DIR, f"{video_id}.mp3")
    db = db_read()

    # Verificar si existe el archivo y está en la base de datos
    if os.path.exists(file_path) and video_id in db:
        file_size = os.path.getsize(file_path)
        return JSONResponse({
            "ready": True,
            "id": video_id,
            "status": "completed",
            "progress": 100,
            "file_size": file_size
        }, status_code=200)
    else:
        # Verificar si está en proceso de descarga
        partial_file = os.path.join(MEDIA_DIR, f"{video_id}.mp3.part")
        temp_file = os.path.join(MEDIA_DIR, f"{video_id}.temp")

        progress = 0
        status = "not_found"

        # Buscar archivos temporales o parciales
        for temp_path in [partial_file, temp_file]:
            if os.path.exists(temp_path):
                status = "downloading"
                # Estimar progreso basado en tamaño del archivo temporal
                temp_size = os.path.getsize(temp_path)
                if temp_size > 0:
                    # Estimación muy básica - en un caso real necesitarías más información
                    progress = min(50, (temp_size // 1024) // 10)  # Progreso estimado
                break

        # Siempre devolver 200 para que el frontend pueda procesar el estado
        return JSONResponse({
            "ready": False,
            "id": video_id,
            "status": status,
            "progress": progress
        }, status_code=200)

# -------- Rooms REST API --------
@app.get("/api/rooms")
async def api_list_rooms():
    """Lista todas las rooms disponibles"""
    all_rooms = rooms_read()
    return {"rooms": list(all_rooms.values())}

@app.post("/api/rooms")
async def api_create_room(
    name: str = Body(...),
    user_id: Optional[str] = Body(None),
    is_public: bool = Body(True),
    password: Optional[str] = Body(None)
):
    """Crea una nueva room (pública o privada con contraseña)"""
    import uuid
    room_id = str(uuid.uuid4())[:8]

    # Si es privada y tiene contraseña, hashearla
    password_hash = None
    if not is_public and password:
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    room = await get_or_create_room(room_id, user_id, is_public, password_hash)
    room.name = name
    await save_room_state(room)

    return {
        "room_id": room_id,
        "name": name,
        "is_public": is_public,
        "requires_password": not is_public and password_hash is not None
    }

@app.post("/api/rooms/{room_id}/join")
async def api_join_room(room_id: str, request: Request):
    """Intenta unirse a una room (verifica contraseña si es privada)"""
    import uuid

    # Leer el body como JSON
    try:
        body = await request.json()
        password = body.get("password")
    except:
        password = None

    print(f"[JOIN] room_id={room_id}, password={'***' if password else 'None'}")

    # Obtener la room
    all_rooms = rooms_read()
    if room_id not in all_rooms:
        return JSONResponse({"error": "Room no encontrada"}, status_code=404)

    room_data = all_rooms[room_id]
    is_public = room_data.get("is_public", True)
    password_hash = room_data.get("password_hash")

    print(f"[JOIN] is_public={is_public}, has_hash={password_hash is not None}")

    # Si es pública, permitir acceso directo
    if is_public:
        session_token = str(uuid.uuid4())
        async with _sessions_lock:
            sessions = sessions_read()
            sessions[session_token] = {"room_id": room_id, "created_at": time.time()}
            sessions_write(sessions)
        return {
            "success": True,
            "session_token": session_token,
            "room_id": room_id,
            "is_public": True
        }

    # Si es privada, verificar contraseña
    if not password:
        return JSONResponse({"error": "Contraseña requerida"}, status_code=401)

    if not password_hash:
        return JSONResponse({"error": "Room privada sin contraseña configurada"}, status_code=500)

    # Verificar contraseña con bcrypt
    if not bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
        return JSONResponse({"error": "Contraseña incorrecta"}, status_code=401)

    # Contraseña correcta, generar token de sesión
    session_token = str(uuid.uuid4())
    async with _sessions_lock:
        sessions = sessions_read()
        sessions[session_token] = {"room_id": room_id, "created_at": time.time()}
        sessions_write(sessions)

    return {
        "success": True,
        "session_token": session_token,
        "room_id": room_id,
        "is_public": False
    }

@app.get("/api/rooms/{room_id}")
async def api_get_room(room_id: str):
    """Obtiene información de una room"""
    room = await get_or_create_room(room_id)
    return {
        "id": room.id,
        "name": room.name,
        "created_by": room.created_by,
        "is_public": room.is_public,
        "requires_password": not room.is_public and room.password_hash is not None,
        "queue": await room.get_queue_detailed(),
        "state": room.public_state()
    }

@app.delete("/api/rooms/{room_id}")
async def api_delete_room(room_id: str, user_id: str = Body(...)):
    """Elimina una room (solo el creador puede eliminarla)"""
    # No permitir eliminar la room default
    if room_id == "default":
        return JSONResponse({"error": "No se puede eliminar la room por defecto"}, status_code=403)

    all_rooms = rooms_read()
    if room_id not in all_rooms:
        return JSONResponse({"error": "Room no encontrada"}, status_code=404)

    room_data = all_rooms[room_id]
    created_by = room_data.get("created_by")

    # Verificar que el usuario sea el creador
    if created_by != user_id:
        return JSONResponse({"error": "Solo el creador puede eliminar esta room"}, status_code=403)

    # Eliminar la room de memoria y del archivo
    async with rooms_lock:
        if room_id in rooms:
            # Cerrar todas las conexiones WebSocket de esta room
            room = rooms[room_id]
            for ws in list(room.connections):
                try:
                    await ws.send_json({"type": "room_deleted", "data": {"message": "Esta room ha sido eliminada"}})
                    await ws.close()
                except:
                    pass
            del rooms[room_id]

        # Eliminar del archivo
        all_rooms = rooms_read()
        if room_id in all_rooms:
            del all_rooms[room_id]
            rooms_write(all_rooms)

    return {"success": True, "message": "Room eliminada correctamente"}

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("index.html")

# -------- WebSocket con Rooms --------
@app.websocket("/ws/{room_id}")
async def ws_room_endpoint(ws: WebSocket, room_id: str):
    await ws.accept()

    # Obtener session_token de query params
    session_token = ws.query_params.get("session_token")

    # Verificar acceso a la room
    all_rooms = rooms_read()
    if room_id not in all_rooms:
        await ws.send_json({"type":"room_not_found","data":{"message":"Room no encontrada, redirigiendo a default..."}})
        await ws.close()
        return

    room_data = all_rooms[room_id]
    is_public = room_data.get("is_public", True)

    print(f"[WS] Intento de conexión a room {room_id}, is_public={is_public}, has_token={session_token is not None}")

    # Si es privada, verificar session_token
    if not is_public:
        if not session_token:
            print(f"[WS] Rechazado: room privada sin token")
            await ws.send_json({"type":"error","data":{"message":"Token de sesión requerido para room privada"}})
            await ws.close()
            return

        # Validar token
        sessions = sessions_read()
        if session_token not in sessions:
            print(f"[WS] Rechazado: token inválido")
            await ws.send_json({"type":"error","data":{"message":"Token de sesión inválido"}})
            await ws.close()
            return

        # Verificar que el token sea para esta room
        if sessions[session_token].get("room_id") != room_id:
            print(f"[WS] Rechazado: token para otra room")
            await ws.send_json({"type":"error","data":{"message":"Token no válido para esta room"}})
            await ws.close()
            return

    print(f"[WS] Conexión aceptada a room {room_id}")

    # Obtener o crear la room
    room = await get_or_create_room(room_id)
    room.connections.add(ws)

    try:
        # Enviar estado inicial de la room
        await ws.send_json({"type":"state","data":{
            **room.public_state(),
            "queue": await room.get_queue_detailed(),
            "room_id": room_id,
            "room_name": room.name
        }})

        while True:
            msg = await ws.receive_json()
            t = msg.get("type")

            if t == "queue:add":
                url_or_id = msg.get("urlOrId") or msg.get("id")
                hybrid_mode = msg.get("hybrid", False)  # Nuevo parámetro para modo híbrido
                if not url_or_id:
                    await ws.send_json({"type":"error","data":{"message":"urlOrId requerido"}});
                    continue
                try:
                    if hybrid_mode:
                        # Modo híbrido: agregar directamente sin procesar con yt-dlp
                        print(f"[HYBRID] Agregando track {url_or_id} en modo híbrido (sin yt-dlp)")
                        # Crear record básico para la base de datos sin procesar
                        rec = {
                            "id": url_or_id,
                            "title": f"YouTube Video {url_or_id}",  # Título placeholder
                            "mode": "hybrid",
                            "timestamp": time.time()
                        }
                        # Guardar en base de datos
                        db = db_read()
                        db[url_or_id] = rec
                        db_write(db)
                        await ws.send_json({"type":"ok","data":{"action":"queue:add","id":rec["id"]}})
                    else:
                        # Modo tradicional: procesar con yt-dlp
                        rec = await ensure_track(url_or_id)
                        if rec.get("mode") == "unavailable":
                            await ws.send_json({
                                "type":"warning",
                                "data":{
                                    "message":"Track agregado pero puede no reproducirse debido a restricciones de YouTube",
                                    "track": {"id": rec["id"], "title": rec["title"]},
                                    "action":"queue:add"
                                }
                            })
                        else:
                            await ws.send_json({"type":"ok","data":{"action":"queue:add","id":rec["id"]}})
                    await room_enqueue_track(room, rec["id"])
                except Exception as e:
                    error_msg = str(e)
                    if "Servicio temporalmente no disponible" in error_msg:
                        error_msg = "YouTube está bloqueando las solicitudes. Intenta más tarde."
                    await ws.send_json({"type":"error","data":{"message":error_msg}})

            elif t == "player:play":
                await room_cmd_play(room, msg.get("at"))
            elif t == "player:pause":
                await room_cmd_pause(room)
            elif t == "player:seek":
                await room_cmd_seek(room, float(msg.get("at") or 0))
            elif t == "player:next":
                await room_cmd_next(room)
            elif t == "state:get":
                await ws.send_json({"type":"state","data":{
                    **room.public_state(),
                    "queue": await room.get_queue_detailed(),
                    "room_id": room_id
                }})

    except WebSocketDisconnect:
        room.connections.discard(ws)

# WebSocket legacy (default room) para compatibilidad con frontend actual
@app.websocket("/ws")
async def ws_endpoint_legacy(ws: WebSocket):
    # Redirigir a room "default"
    await ws_room_endpoint(ws, "default")
