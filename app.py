import os
from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

# Enable CORS for all routes
CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent"
)

# { class_id: { slide_index: image_dataURL_or_None } }
canvas_data = {}

# { class_id: current_slide_index }
current_slide = {}


@app.route("/")
def home():
    return "Server Running ✅"


# =========================
# HELPERS
# =========================
def get_slide(room):
    return current_slide.get(room, 0)

def ensure_room(room):
    canvas_data.setdefault(room, {0: None})
    current_slide.setdefault(room, 0)


# =========================
# JOIN ROOM
# =========================
@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)
    ensure_room(room)

    slide = current_slide[room]
    image = canvas_data[room].get(slide)

    # Send current canvas state (as image) to the joining client only
    emit("load-canvas", {
        "image": image,
        "slide": slide
    })

    # Notify others that a new user joined (for WebRTC offer)
    emit("user-joined", {
        "user_id": request.sid
    }, room=room, include_self=False)


# =========================
# WEBRTC SIGNALING
# =========================
@socketio.on("offer")
def offer(data):
    emit("offer", {
        "offer": data["offer"],
        "from": request.sid
    }, to=data["to"])


@socketio.on("answer")
def answer(data):
    emit("answer", {
        "answer": data["answer"],
        "from": request.sid
    }, to=data["to"])


@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {
        "candidate": data["candidate"],
        "from": request.sid
    }, to=data["to"])


# =========================
# DRAWING (live stroke relay — no storage needed, image is source of truth)
# =========================
@socketio.on("draw-start")
def draw_start(data):
    room = data["class_id"]
    emit("draw-start", data, room=room, include_self=False)


@socketio.on("draw")
def draw(data):
    room = data["class_id"]
    emit("draw", data, room=room, include_self=False)


@socketio.on("erase")
def erase(data):
    room = data["class_id"]
    emit("erase", data, room=room, include_self=False)


@socketio.on("draw-end")
def draw_end(data):
    room = data["class_id"]
    emit("draw-end", {}, room=room, include_self=False)


# =========================
# CANVAS IMAGE (source of truth for persistence)
# =========================
@socketio.on("canvas-image")
def canvas_image(data):
    room = data["class_id"]
    slide = data.get("slide", get_slide(room))
    image = data.get("image")

    ensure_room(room)
    canvas_data[room][slide] = image

    # Relay to all other clients so they stay in sync
    emit("canvas-image-update", {
        "slide": slide,
        "image": image
    }, room=room, include_self=False)


# =========================
# CLEAR
# =========================
@socketio.on("clear-canvas")
def clear_canvas(data):
    room = data["class_id"]
    slide = get_slide(room)

    ensure_room(room)
    canvas_data[room][slide] = None

    emit("clear-canvas", {}, room=room, include_self=False)


# =========================
# SLIDES
# =========================
@socketio.on("add-slide")
def add_slide(data):
    room = data["class_id"]
    ensure_room(room)

    slides = canvas_data[room]
    new_index = len(slides)
    slides[new_index] = None
    current_slide[room] = new_index

    emit("slide-changed", {
        "slide": new_index,
        "image": None
    }, room=room)


@socketio.on("add-slide-with-image")
def add_slide_with_image(data):
    room = data["class_id"]
    image = data.get("image")
    ensure_room(room)

    slides = canvas_data[room]
    new_index = len(slides)
    slides[new_index] = image
    current_slide[room] = new_index

    emit("slide-changed", {
        "slide": new_index,
        "image": image
    }, room=room)


@socketio.on("change-slide")
def change_slide(data):
    room = data["class_id"]
    slide = data["slide"]
    ensure_room(room)

    slides = canvas_data[room]
    slide = max(0, min(slide, len(slides) - 1))
    current_slide[room] = slide

    emit("slide-changed", {
        "slide": slide,
        "image": slides.get(slide)
    }, room=room)


@socketio.on("delete-slide")
def delete_slide(data):
    room = data["class_id"]
    idx = data.get("slide", 0)
    ensure_room(room)

    slides = canvas_data[room]
    if len(slides) <= 1:
        # Can't delete the last slide — just clear it
        slides[0] = None
        current_slide[room] = 0
        emit("slide-changed", {"slide": 0, "image": None}, room=room)
        return

    # Rebuild dict without the deleted index
    new_slides = {}
    new_idx = 0
    for i in sorted(slides.keys()):
        if i != idx:
            new_slides[new_idx] = slides[i]
            new_idx += 1
    canvas_data[room] = new_slides

    # Clamp current slide
    cur = current_slide[room]
    if cur >= idx:
        cur = max(0, cur - 1)
    current_slide[room] = cur

    emit("slide-changed", {
        "slide": cur,
        "image": new_slides.get(cur)
    }, room=room)


@socketio.on("get-slides")
def get_slides(data):
    room = data["class_id"]
    ensure_room(room)

    slides = canvas_data[room]
    ordered = [slides.get(i) for i in sorted(slides.keys())]

    emit("slides-list", {
        "slides": ordered,
        "current": current_slide.get(room, 0)
    })


# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
