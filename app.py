from flask import Flask
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

@app.route("/")
def home():
    return "<html> <h2>Welcome to the server by Hamzah</h2></html>"

@socketio.on("join")
def join(data):
    room = data["room"]
    join_room(room)
    emit("user-joined", room=room, include_self=False)

@socketio.on("offer")
def offer(data):
    emit("offer", data["offer"], room=data["room"], include_self=False)

@socketio.on("answer")
def answer(data):
    emit("answer", data["answer"], room=data["room"], include_self=False)

@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", data["candidate"], room=data["room"], include_self=False)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
