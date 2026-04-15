from flask import Flask, request
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

socketio = SocketIO(app, cors_allowed_origins="*")

# =========================
# 🧠 STORE CANVAS DATA
# =========================
# { class_id: [ {type, x, y} ] }
canvas_data = {}

@app.route("/")
def home():
    return "Server Running ✅"


# =========================
# 🔥 JOIN ROOM
# =========================
@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)

    print("User joined:", request.sid, "Room:", room)

    # 🔥 SEND PREVIOUS DRAWING TO NEW USER
    old_data = canvas_data.get(room, [])
    emit("load-canvas", old_data)

    # Notify others
    emit("user-joined", {
        "user_id": request.sid
    }, room=room, include_self=False)


# =========================
# 🔥 WEBRTC SIGNALING
# =========================

# OFFER
@socketio.on("offer")
def offer(data):
    emit("offer", {
        "offer": data["offer"],
        "from": request.sid
    }, to=data["to"])


# ANSWER
@socketio.on("answer")
def answer(data):
    emit("answer", {
        "answer": data["answer"],
        "from": request.sid
    }, to=data["to"])


# ICE CANDIDATE
@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {
        "candidate": data["candidate"],
        "from": request.sid
    }, to=data["to"])


# =========================
# 🎨 CANVAS DRAWING
# =========================

# DRAW START
@socketio.on("draw-start")
def handle_draw_start(data):
    room = data["class_id"]

    canvas_data.setdefault(room, []).append({
        "type": "start",
        "x": data["x"],
        "y": data["y"]
    })

    emit("draw-start", {
        "x": data["x"],
        "y": data["y"]
    }, room=room, include_self=False)


# DRAW MOVE
@socketio.on("draw")
def handle_draw(data):
    room = data["class_id"]

    canvas_data.setdefault(room, []).append({
        "type": "draw",
        "x": data["x"],
        "y": data["y"]
    })

    emit("draw", {
        "x": data["x"],
        "y": data["y"]
    }, room=room, include_self=False)


# DRAW END
@socketio.on("draw-end")
def handle_draw_end(data):
    room = data["class_id"]

    canvas_data.setdefault(room, []).append({
        "type": "end"
    })

    emit("draw-end", {}, room=room, include_self=False)


# =========================
# 🧽 CLEAR CANVAS
# =========================
@socketio.on("clear-canvas")
def clear_canvas(data):
    room = data["class_id"]

    # 🔥 RESET STORED DATA
    canvas_data[room] = []

    emit("clear-canvas", {}, room=room)


# =========================
# 🚀 RUN SERVER
# =========================
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
