import os
from flask import Flask, request
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent"
)

# { class_id: { slide_index: [draw_data] } }
canvas_data = {}

# { class_id: current_slide_index }
current_slide = {}


@app.route("/")
def home():
    return "Server Running ✅"


# =========================
# JOIN ROOM
# =========================
@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)

    canvas_data.setdefault(room, {})
    current_slide.setdefault(room, 0)

    slide = current_slide[room]
    old_data = canvas_data[room].get(slide, [])

    # Send existing canvas state to the joining client only
    emit("load-canvas", {
        "data": old_data,
        "slide": slide
    })

    # Notify others in the room that a new user joined
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
# DRAWING
# =========================
def get_slide(room):
    return current_slide.get(room, 0)


@socketio.on("draw-start")
def draw_start(data):
    room = data["class_id"]
    slide = get_slide(room)

    canvas_data.setdefault(room, {}).setdefault(slide, []).append({
        "type": "start",
        "x": data["x"],
        "y": data["y"]
    })

    # Broadcast to everyone else in the room
    emit("draw-start", data, room=room, include_self=False)


@socketio.on("draw")
def draw(data):
    room = data["class_id"]
    slide = get_slide(room)

    canvas_data[room][slide].append({
        "type": "draw",
        "x": data["x"],
        "y": data["y"]
    })

    emit("draw", data, room=room, include_self=False)


@socketio.on("draw-end")
def draw_end(data):
    room = data["class_id"]
    slide = get_slide(room)

    canvas_data[room][slide].append({
        "type": "end"
    })

    emit("draw-end", {}, room=room, include_self=False)


# =========================
# CLEAR
# =========================
@socketio.on("clear-canvas")
def clear_canvas(data):
    room = data["class_id"]
    slide = get_slide(room)

    canvas_data[room][slide] = []

    # FIX: include_self=False so teacher doesn't double-clear locally
    emit("clear-canvas", {}, room=room, include_self=False)


# =========================
# SLIDES
# =========================
@socketio.on("add-slide")
def add_slide(data):
    room = data["class_id"]

    slides = canvas_data.setdefault(room, {})
    new_index = len(slides)

    slides[new_index] = []
    current_slide[room] = new_index

    # Broadcast to everyone (teacher included so UI updates)
    emit("slide-changed", {
        "slide": new_index,
        "data": []
    }, room=room)


@socketio.on("change-slide")
def change_slide(data):
    room = data["class_id"]
    slide = data["slide"]

    # Clamp to valid range
    slides = canvas_data.get(room, {})
    slide = max(0, min(slide, len(slides) - 1))

    current_slide[room] = slide
    slide_data = slides.get(slide, [])

    # Broadcast to everyone
    emit("slide-changed", {
        "slide": slide,
        "data": slide_data
    }, room=room)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
