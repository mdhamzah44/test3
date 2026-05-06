import os
from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

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

# =============================================
# POLL STATE
# { class_id: {
#     poll_id, poll_type, options, timer,
#     question_num, correct (None until ended),
#     responses: { user_id: answer_index },
#     active: bool
# } }
# =============================================
poll_state = {}


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

def ensure_poll(room):
    poll_state.setdefault(room, {
        "poll_id": None,
        "active": False,
        "responses": {}
    })


# =========================
# JOIN ROOM
# =========================
@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)
    ensure_room(room)
    ensure_poll(room)

    slide = current_slide[room]
    image = canvas_data[room].get(slide)

    emit("load-canvas", {
        "image": image,
        "slide": slide
    })

    emit("user-joined", {
        "user_id": request.sid
    }, room=room, include_self=False)

    # If there's an active poll, send it to the newly joined student
    poll = poll_state.get(room, {})
    if poll.get("active"):
        emit("poll-start", {
            "poll_id": poll["poll_id"],
            "poll_type": poll["poll_type"],
            "options": poll["options"],
            "timer": poll.get("timer_remaining", poll.get("timer", 30)),
            "question_num": poll.get("question_num", 1)
        })


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
# CANVAS IMAGE
# =========================
@socketio.on("canvas-image")
def canvas_image(data):
    room = data["class_id"]
    slide = data.get("slide", get_slide(room))
    image = data.get("image")

    ensure_room(room)
    canvas_data[room][slide] = image

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
    emit("slide-changed", {"slide": new_index, "image": None}, room=room)


@socketio.on("add-slide-with-image")
def add_slide_with_image(data):
    room = data["class_id"]
    image = data.get("image")
    ensure_room(room)
    slides = canvas_data[room]
    new_index = len(slides)
    slides[new_index] = image
    current_slide[room] = new_index
    emit("slide-changed", {"slide": new_index, "image": image}, room=room)


@socketio.on("change-slide")
def change_slide(data):
    room = data["class_id"]
    slide = data["slide"]
    ensure_room(room)
    slides = canvas_data[room]
    slide = max(0, min(slide, len(slides) - 1))
    current_slide[room] = slide
    emit("slide-changed", {"slide": slide, "image": slides.get(slide)}, room=room)


@socketio.on("delete-slide")
def delete_slide(data):
    room = data["class_id"]
    idx = data.get("slide", 0)
    ensure_room(room)
    slides = canvas_data[room]

    if len(slides) <= 1:
        slides[0] = None
        current_slide[room] = 0
        emit("slide-changed", {"slide": 0, "image": None}, room=room)
        return

    new_slides = {}
    new_idx = 0
    for i in sorted(slides.keys()):
        if i != idx:
            new_slides[new_idx] = slides[i]
            new_idx += 1
    canvas_data[room] = new_slides

    cur = current_slide[room]
    if cur >= idx:
        cur = max(0, cur - 1)
    current_slide[room] = cur

    emit("slide-changed", {"slide": cur, "image": new_slides.get(cur)}, room=room)


@socketio.on("get-slides")
def get_slides(data):
    room = data["class_id"]
    ensure_room(room)
    slides = canvas_data[room]
    ordered = [slides.get(i) for i in sorted(slides.keys())]
    emit("slides-list", {"slides": ordered, "current": current_slide.get(room, 0)})


# =========================
# POLL SYSTEM
# =========================

@socketio.on("poll-start")
def poll_start(data):
    """
    Teacher starts a poll.
    data: { class_id, poll_id, poll_type, options, timer, question_num }
    """
    room = data["class_id"]
    ensure_poll(room)

    poll_state[room] = {
        "poll_id": data["poll_id"],
        "poll_type": data.get("poll_type"),
        "options": data.get("options", []),
        "timer": data.get("timer", 30),
        "timer_remaining": data.get("timer", 30),
        "question_num": data.get("question_num", 1),
        "active": True,
        "responses": {}
    }

    # Broadcast to ALL in room (including teacher for confirmation)
    emit("poll-start", {
        "poll_id": data["poll_id"],
        "poll_type": data.get("poll_type"),
        "options": data.get("options", []),
        "timer": data.get("timer", 30),
        "question_num": data.get("question_num", 1)
    }, room=room, include_self=False)


@socketio.on("poll-response")
def poll_response(data):
    """
    Student submits poll answer.
    data: { class_id, poll_id, user_id, answer (int index), name }
    """
    room = data["class_id"]
    ensure_poll(room)

    poll = poll_state[room]

    # Validate poll is still active and IDs match
    if not poll.get("active"):
        return
    if poll.get("poll_id") != data.get("poll_id"):
        return

    user_id = data.get("user_id") or request.sid
    answer = data.get("answer")
    name = data.get("name", "Student")

    # Store response (one per user — last write wins if they somehow submit twice)
    poll["responses"][user_id] = answer

    # Store name mapping for leaderboard
    if "_names" not in poll:
        poll["_names"] = {}
    poll["_names"][user_id] = name

    # Relay to teacher so live bars update
    emit("poll-response", {
        "poll_id": data["poll_id"],
        "user_id": user_id,
        "answer": answer,
        "name": name,
        "total": len(poll["responses"])
    }, room=room, include_self=True)


@socketio.on("poll-end")
def poll_end(data):
    """
    Teacher ends the poll (timer or manual).
    data: { class_id, poll_id, correct (int), responses (dict) }
    """
    room = data["class_id"]
    ensure_poll(room)

    poll = poll_state[room]
    poll["active"] = False
    poll["correct"] = data.get("correct")

    # Use server-side responses (authoritative) merged with client-side
    server_responses = poll.get("responses", {})
    client_responses = data.get("responses", {})

    # Merge: server is authoritative but fill in any client-only entries
    merged = {**client_responses, **server_responses}
    poll["responses"] = merged

    # Broadcast end + results to all students
    emit("poll-end", {
        "poll_id": data["poll_id"],
        "correct": data.get("correct"),
        "responses": merged
    }, room=room, include_self=False)


@socketio.on("show-leaderboard")
def show_leaderboard(data):
    """
    Teacher broadcasts leaderboard to all students.
    data: { class_id, leaderboard: [{rank, name, score, correct, wrong}] }
    """
    room = data["class_id"]
    emit("show-leaderboard", {
        "leaderboard": data.get("leaderboard", [])
    }, room=room, include_self=False)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
