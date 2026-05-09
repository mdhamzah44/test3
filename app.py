import os
import time
import zlib
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "smartedu-secret")

CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent",
    ping_timeout=60,
    ping_interval=20,
    max_http_buffer_size=80 * 1024 * 1024,   # 80 MB — large PDF batches
    # compression reduces WebSocket frame sizes significantly
    compression_threshold=1024,               # compress payloads > 1 KB
    logger=False,                             # disable verbose logging in prod
    engineio_logger=False,
)

# ---------------------------------------------------------------------------
# IN-MEMORY STATE
# ---------------------------------------------------------------------------
# canvas_data[room][slide] = compressed image bytes | None
canvas_data: dict[str, dict[int, bytes | None]] = {}

# current_slide[room] = int
current_slide: dict[str, int] = {}

# slide_meta[room][slide] = {"dark": bool}
slide_meta: dict[str, dict[int, dict]] = {}

# poll_state[room] = {...}
poll_state: dict[str, dict] = {}

# hand_raise_state[room][user_id] = {"name", "socket_id", "raised_at"}
hand_raise_state: dict[str, dict] = {}

# voice_call_state[room] = {"student_id", "teacher_socket_id", "student_socket_id"}
voice_call_state: dict[str, dict] = {}

# sid_map[sid] = {"class_id", "user_id"}
sid_map: dict[str, dict] = {}

# draw throttle: last broadcast time per room (canvas-image)
last_canvas_broadcast: dict[str, float] = {}
CANVAS_BROADCAST_MIN_INTERVAL = 0.08   # 80 ms ≈ 12 fps max image sync

# ---------------------------------------------------------------------------
# COMPRESSION HELPERS
# ---------------------------------------------------------------------------

def compress_image(data_url: str) -> bytes:
    """Compress a data URL string with zlib for storage."""
    return zlib.compress(data_url.encode("utf-8"), level=1)   # level=1 = fastest


def decompress_image(data: bytes) -> str:
    """Decompress stored bytes back to data URL string."""
    return zlib.decompress(data).decode("utf-8")


def get_slide_image(room: str, slide: int) -> str | None:
    raw = canvas_data.get(room, {}).get(slide)
    if raw is None:
        return None
    return decompress_image(raw)


def set_slide_image(room: str, slide: int, data_url: str | None):
    if data_url is None:
        canvas_data.setdefault(room, {})[slide] = None
    else:
        canvas_data.setdefault(room, {})[slide] = compress_image(data_url)


# ---------------------------------------------------------------------------
# INIT HELPERS
# ---------------------------------------------------------------------------

def ensure_room(room: str):
    canvas_data.setdefault(room, {0: None})
    current_slide.setdefault(room, 0)
    slide_meta.setdefault(room, {0: {"dark": False}})


def ensure_poll(room: str):
    poll_state.setdefault(room, {
        "poll_id": None, "active": False,
        "responses": {}, "_names": {}
    })


def ensure_hand(room: str):
    hand_raise_state.setdefault(room, {})


def ensure_voice(room: str):
    voice_call_state.setdefault(room, {
        "student_id": None,
        "teacher_socket_id": None,
        "student_socket_id": None,
    })


def get_slide(room: str) -> int:
    return current_slide.get(room, 0)


def student_socket_for(room: str, user_id: str) -> str | None:
    """O(1) lookup via stored socket_id in hand raise state."""
    info = hand_raise_state.get(room, {}).get(user_id)
    if info:
        return info.get("socket_id")
    # Fallback: check voice_call_state
    voice = voice_call_state.get(room, {})
    if voice.get("student_id") == user_id:
        return voice.get("student_socket_id")
    return None


# ---------------------------------------------------------------------------
# HEALTH
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    rooms = len(canvas_data)
    sids = len(sid_map)
    return jsonify({"status": "ok", "rooms": rooms, "connections": sids})


# ---------------------------------------------------------------------------
# HTTP: get slides for PDF download
# ---------------------------------------------------------------------------

@app.route("/get-slides-pdf/<class_id>")
def get_slides_pdf(class_id: str):
    ensure_room(class_id)
    slides_dict = canvas_data.get(class_id, {})
    ordered = [
        get_slide_image(class_id, i)
        for i in sorted(slides_dict.keys())
    ]
    valid = [s for s in ordered if s]
    return jsonify({"slides": valid, "total": len(valid)})


# ---------------------------------------------------------------------------
# JOIN / DISCONNECT
# ---------------------------------------------------------------------------

@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)
    ensure_room(room)
    ensure_poll(room)
    ensure_hand(room)
    ensure_voice(room)

    user_id = data.get("user_id") or request.sid
    sid_map[request.sid] = {"class_id": room, "user_id": user_id}

    slide = current_slide[room]
    image = get_slide_image(room, slide)
    dark = slide_meta.get(room, {}).get(slide, {}).get("dark", False)

    emit("load-canvas", {"image": image, "slide": slide, "dark": dark})

    # Notify teacher of new viewer
    emit("user-joined", {"user_id": request.sid}, room=room, include_self=False)

    # Replay active poll for late joiners
    poll = poll_state.get(room, {})
    if poll.get("active"):
        elapsed = time.time() - poll.get("started_at", time.time())
        remaining = max(1, int(poll.get("timer", 30) - elapsed))
        emit("poll-start", {
            "poll_id": poll["poll_id"],
            "poll_type": poll["poll_type"],
            "options": poll["options"],
            "timer": remaining,
            "question_num": poll.get("question_num", 1),
        })

    # Re-send existing hand raises to this client (teacher reconnect)
    for uid, info in hand_raise_state.get(room, {}).items():
        emit("hand-raise", {
            "user_id": uid,
            "name": info.get("name", "Student"),
            "raised": True,
        })


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    info = sid_map.pop(sid, None)
    if not info:
        return

    room = info.get("class_id")
    if not room:
        return

    user_id = info.get("user_id")

    # Clean up hand raise
    hands = hand_raise_state.get(room, {})
    if user_id in hands and hands[user_id].get("socket_id") == sid:
        del hands[user_id]
        socketio.emit("hand-raise", {"user_id": user_id, "name": "", "raised": False}, room=room)

    # Clean up voice call
    voice = voice_call_state.get(room, {})
    if voice.get("student_id") == user_id or voice.get("student_socket_id") == sid:
        teacher_sid = voice.get("teacher_socket_id")
        voice_call_state[room] = {"student_id": None, "teacher_socket_id": None, "student_socket_id": None}
        if teacher_sid:
            socketio.emit("voice-ended-by-student", {"student_id": user_id}, to=teacher_sid)


# ---------------------------------------------------------------------------
# WEBRTC SIGNALING — teacher video → students
# ---------------------------------------------------------------------------

@socketio.on("offer")
def offer(data):
    emit("offer", {"offer": data["offer"], "from": request.sid}, to=data["to"])


@socketio.on("answer")
def answer(data):
    emit("answer", {"answer": data["answer"], "from": request.sid}, to=data["to"])


@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {"candidate": data["candidate"], "from": request.sid}, to=data["to"])


# ---------------------------------------------------------------------------
# REAL-TIME DRAWING — normalized coords, forward immediately (no extra work)
# ---------------------------------------------------------------------------

@socketio.on("draw-start")
def draw_start(data):
    emit("draw-start", data, room=data["class_id"], include_self=False)


@socketio.on("draw")
def draw(data):
    emit("draw", data, room=data["class_id"], include_self=False)


@socketio.on("erase")
def erase(data):
    emit("erase", data, room=data["class_id"], include_self=False)


@socketio.on("draw-end")
def draw_end(data):
    emit("draw-end", {}, room=data["class_id"], include_self=False)


# ---------------------------------------------------------------------------
# CANVAS IMAGE SYNC — throttled storage + forward
# ---------------------------------------------------------------------------

@socketio.on("canvas-image")
def canvas_image(data):
    """
    Throttle: store + relay canvas snapshots.
    The teacher frontend already throttles sends to ~12 fps;
    here we also skip relay if the last relay was < 80ms ago.
    """
    room = data["class_id"]
    slide = data.get("slide", get_slide(room))
    image: str | None = data.get("image")

    ensure_room(room)
    set_slide_image(room, slide, image)   # compressed storage

    now = time.time()
    last = last_canvas_broadcast.get(room, 0)
    if now - last >= CANVAS_BROADCAST_MIN_INTERVAL:
        last_canvas_broadcast[room] = now
        emit("canvas-image-update", {"slide": slide, "image": image},
             room=room, include_self=False)


@socketio.on("canvas-bg")
def canvas_bg(data):
    room = data["class_id"]
    slide = data.get("slide", get_slide(room))
    dark = bool(data.get("dark", False))
    slide_meta.setdefault(room, {})[slide] = {"dark": dark}
    emit("canvas-bg", {"dark": dark, "slide": slide},
         room=room, include_self=False)


@socketio.on("clear-canvas")
def clear_canvas(data):
    room = data["class_id"]
    slide = get_slide(room)
    set_slide_image(room, slide, None)
    emit("clear-canvas", {}, room=room, include_self=False)


# ---------------------------------------------------------------------------
# SLIDES
# ---------------------------------------------------------------------------

def _emit_slide_changed(room: str, slide: int):
    image = get_slide_image(room, slide)
    dark = slide_meta.get(room, {}).get(slide, {}).get("dark", False)
    emit("slide-changed", {"slide": slide, "image": image, "dark": dark}, room=room)


@socketio.on("add-slide")
def add_slide(data):
    room = data["class_id"]
    ensure_room(room)
    new_index = len(canvas_data[room])
    canvas_data[room][new_index] = None
    current_slide[room] = new_index
    emit("slide-changed", {"slide": new_index, "image": None, "dark": False}, room=room)


@socketio.on("add-slide-with-image")
def add_slide_with_image(data):
    room = data["class_id"]
    ensure_room(room)
    image = data.get("image")
    dark = bool(data.get("dark", False))
    new_index = len(canvas_data[room])
    set_slide_image(room, new_index, image)
    slide_meta.setdefault(room, {})[new_index] = {"dark": dark}
    current_slide[room] = new_index
    emit("slide-changed", {"slide": new_index, "image": image, "dark": dark}, room=room)


@socketio.on("change-slide")
def change_slide(data):
    room = data["class_id"]
    ensure_room(room)
    slides = canvas_data[room]
    slide = max(0, min(int(data["slide"]), len(slides) - 1))
    current_slide[room] = slide
    _emit_slide_changed(room, slide)


@socketio.on("delete-slide")
def delete_slide(data):
    room = data["class_id"]
    idx = int(data.get("slide", 0))
    ensure_room(room)
    slides = canvas_data[room]

    if len(slides) <= 1:
        canvas_data[room] = {0: None}
        slide_meta.setdefault(room, {})[0] = {"dark": False}
        current_slide[room] = 0
        emit("slide-changed", {"slide": 0, "image": None, "dark": False}, room=room)
        return

    new_slides: dict[int, bytes | None] = {}
    new_meta: dict[int, dict] = {}
    old_meta = slide_meta.get(room, {})
    new_idx = 0
    for i in sorted(slides.keys()):
        if i != idx:
            new_slides[new_idx] = slides[i]
            new_meta[new_idx] = old_meta.get(i, {"dark": False})
            new_idx += 1

    canvas_data[room] = new_slides
    slide_meta[room] = new_meta

    cur = current_slide[room]
    cur = max(0, cur - 1) if cur >= idx else cur
    current_slide[room] = cur
    _emit_slide_changed(room, cur)


@socketio.on("get-slides")
def get_slides(data):
    room = data["class_id"]
    ensure_room(room)
    slides = canvas_data[room]
    ordered = [get_slide_image(room, i) for i in sorted(slides.keys())]
    emit("slides-list", {
        "slides": ordered,
        "current": current_slide.get(room, 0),
    })


# ---------------------------------------------------------------------------
# UPLOAD PROGRESS (teacher → students)
# ---------------------------------------------------------------------------

@socketio.on("upload-start")
def upload_start(data):
    emit("upload-start", {"label": data.get("label", "Loading content…")},
         room=data["class_id"], include_self=False)


@socketio.on("upload-progress")
def upload_progress(data):
    emit("upload-progress", {
        "label": data.get("label", "Loading…"),
        "pct": data.get("pct", 0),
    }, room=data["class_id"], include_self=False)


# ---------------------------------------------------------------------------
# CLASS ENDED
# ---------------------------------------------------------------------------

@socketio.on("class-ended")
def class_ended(data):
    emit("class-ended", {}, room=data["class_id"], include_self=False)


# ---------------------------------------------------------------------------
# POLL SYSTEM
# ---------------------------------------------------------------------------

@socketio.on("poll-start")
def poll_start(data):
    room = data["class_id"]
    ensure_poll(room)
    poll_state[room] = {
        "poll_id": data["poll_id"],
        "poll_type": data.get("poll_type"),
        "options": data.get("options", []),
        "timer": data.get("timer", 30),
        "question_num": data.get("question_num", 1),
        "active": True,
        "started_at": time.time(),
        "responses": {},
        "_names": {},
    }
    emit("poll-start", {
        "poll_id": data["poll_id"],
        "poll_type": data.get("poll_type"),
        "options": data.get("options", []),
        "timer": data.get("timer", 30),
        "question_num": data.get("question_num", 1),
    }, room=room, include_self=False)


@socketio.on("poll-response")
def poll_response(data):
    room = data["class_id"]
    ensure_poll(room)
    poll = poll_state[room]
    if not poll.get("active") or poll.get("poll_id") != data.get("poll_id"):
        return

    user_id = data.get("user_id") or request.sid
    answer = data.get("answer")
    name = data.get("name", "Student")

    poll["responses"][user_id] = answer
    poll["_names"][user_id] = name

    # Only notify teacher (not the whole room)
    emit("poll-response", {
        "poll_id": data["poll_id"],
        "user_id": user_id,
        "answer": answer,
        "name": name,
        "total": len(poll["responses"]),
    }, room=room, include_self=True)


@socketio.on("poll-end")
def poll_end(data):
    room = data["class_id"]
    ensure_poll(room)
    poll = poll_state[room]
    poll["active"] = False
    poll["correct"] = data.get("correct")
    # Merge server-collected responses with any the teacher passed
    merged = {**data.get("responses", {}), **poll.get("responses", {})}
    poll["responses"] = merged

    emit("poll-end", {
        "poll_id": data["poll_id"],
        "correct": data.get("correct"),
        "responses": merged,
    }, room=room, include_self=False)


@socketio.on("show-leaderboard")
def show_leaderboard(data):
    emit("show-leaderboard", {
        "leaderboard": data.get("leaderboard", [])
    }, room=data["class_id"], include_self=False)


# ---------------------------------------------------------------------------
# HAND RAISE
# ---------------------------------------------------------------------------

@socketio.on("hand-raise")
def hand_raise(data):
    room = data["class_id"]
    ensure_hand(room)
    user_id = data.get("user_id") or request.sid
    raised = bool(data.get("raised", True))
    name = data.get("name", "Student")

    if raised:
        hand_raise_state[room][user_id] = {
            "name": name,
            "socket_id": request.sid,
            "raised_at": time.time(),
        }
    else:
        hand_raise_state[room].pop(user_id, None)

    emit("hand-raise", {"user_id": user_id, "name": name, "raised": raised},
         room=room, include_self=False)


@socketio.on("hand-dismissed")
def hand_dismissed(data):
    room = data["class_id"]
    user_id = data.get("user_id")
    ensure_hand(room)

    info = hand_raise_state.get(room, {}).pop(user_id, None)
    student_sid = info.get("socket_id") if info else None

    if student_sid:
        emit("hand-dismissed", {"user_id": user_id}, to=student_sid)
    else:
        emit("hand-dismissed", {"user_id": user_id}, room=room, include_self=False)


# ---------------------------------------------------------------------------
# VOICE CALL (bidirectional WebRTC)
# ---------------------------------------------------------------------------

@socketio.on("voice-accept")
def voice_accept(data):
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    student_socket_id = student_socket_for(room, student_id)

    voice_call_state[room] = {
        "student_id": student_id,
        "teacher_socket_id": request.sid,
        "student_socket_id": student_socket_id,
    }

    payload = {"student_id": student_id, "teacher_socket": request.sid}
    if student_socket_id:
        emit("voice-accept", payload, to=student_socket_id)
    else:
        emit("voice-accept", payload, room=room, include_self=False)


@socketio.on("voice-offer")
def voice_offer(data):
    room = data["class_id"]
    voice = voice_call_state.get(room, {})
    teacher_sid = voice.get("teacher_socket_id")
    if teacher_sid:
        emit("voice-offer", {
            "student_id": data.get("student_id"),
            "offer": data["offer"],
        }, to=teacher_sid)


@socketio.on("voice-answer")
def voice_answer(data):
    room = data["class_id"]
    student_id = data.get("student_id")
    voice = voice_call_state.get(room, {})

    # Use cached socket_id (O(1)) instead of scanning hand queue
    student_sid = voice.get("student_socket_id") or student_socket_for(room, student_id)
    if student_sid:
        emit("voice-answer", {
            "student_id": student_id,
            "answer": data["answer"],
        }, to=student_sid)


@socketio.on("voice-ice")
def voice_ice(data):
    """Route ICE candidates between teacher and student."""
    room = data["class_id"]
    candidate = data.get("candidate")
    from_teacher = bool(data.get("from_teacher", False))
    student_id = data.get("student_id")
    voice = voice_call_state.get(room, {})

    if from_teacher:
        student_sid = voice.get("student_socket_id") or student_socket_for(room, student_id)
        if student_sid:
            emit("voice-ice-student", {"candidate": candidate, "student_id": student_id},
                 to=student_sid)
    else:
        teacher_sid = voice.get("teacher_socket_id")
        if teacher_sid:
            emit("voice-ice-teacher", {"candidate": candidate, "student_id": student_id},
                 to=teacher_sid)


@socketio.on("voice-end")
def voice_end(data):
    """Teacher ends the call."""
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    voice = voice_call_state.get(room, {})
    student_sid = voice.get("student_socket_id") or student_socket_for(room, student_id)

    voice_call_state[room] = {"student_id": None, "teacher_socket_id": None, "student_socket_id": None}
    hand_raise_state.get(room, {}).pop(student_id, None)

    if student_sid:
        emit("voice-end", {"student_id": student_id}, to=student_sid)
    else:
        emit("voice-end", {"student_id": student_id}, room=room, include_self=False)

    socketio.emit("hand-raise", {"user_id": student_id, "name": "", "raised": False}, room=room)


@socketio.on("voice-ended-by-student")
def voice_ended_by_student(data):
    """Student ends the call."""
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    voice = voice_call_state.get(room, {})
    teacher_sid = voice.get("teacher_socket_id")

    voice_call_state[room] = {"student_id": None, "teacher_socket_id": None, "student_socket_id": None}
    hand_raise_state.get(room, {}).pop(student_id, None)

    if teacher_sid:
        emit("voice-ended-by-student", {"student_id": student_id}, to=teacher_sid)

    socketio.emit("hand-raise", {"user_id": student_id, "name": "", "raised": False}, room=room)


# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    workers = int(os.environ.get("WEB_CONCURRENCY", 1))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
