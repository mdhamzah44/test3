from flask import Flask
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

@app.route("/")
def home():
    return "WebRTC Flask Signaling Server Running"

@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)

    emit("user-joined", {
        "user_id": request.sid
    }, room=room, include_self=False)

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

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
