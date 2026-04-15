from flask import Flask, request
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

socketio = SocketIO(app, cors_allowed_origins="*")

@app.route("/")
def home():
    return "Server Running ✅"

# 🔥 JOIN ROOM
@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)

    print("User joined:", request.sid, "Room:", room)

    emit("user-joined", {
        "user_id": request.sid
    }, room=room, include_self=False)

# 🔥 OFFER
@socketio.on("offer")
def offer(data):
    emit("offer", {
        "offer": data["offer"],
        "from": request.sid
    }, to=data["to"])

# 🔥 ANSWER
@socketio.on("answer")
def answer(data):
    emit("answer", {
        "answer": data["answer"],
        "from": request.sid
    }, to=data["to"])

# 🔥 ICE
@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {
        "candidate": data["candidate"],
        "from": request.sid
    }, to=data["to"])


# ✏️ DRAW START
@socketio.on("draw-start")
def handle_draw_start(data):
    room = data["class_id"]

    emit("draw-start", {
        "x": data["x"],
        "y": data["y"]
    }, room=room, include_self=False)


# ✏️ DRAW MOVE
@socketio.on("draw")
def handle_draw(data):
    room = data["class_id"]

    emit("draw", {
        "x": data["x"],
        "y": data["y"]
    }, room=room, include_self=False)


# ✏️ DRAW END
@socketio.on("draw-end")
def handle_draw_end(data):
    room = data["class_id"]

    emit("draw-end", {}, room=room, include_self=False)


@socketio.on("clear-canvas")
def clear_canvas(data):
    emit("clear-canvas", {}, room=data["class_id"])

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
