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

canvas_data = {}
current_slide = {}
poll_state = {}
hand_raise_state = {}
voice_call_state = {}
sid_map = {}


@app.route("/")
def home():
    return "Server Running ✅"


def get_slide(room):
    return current_slide.get(room, 0)

def ensure_room(room):
    canvas_data.setdefault(room, {0: None})
    current_slide.setdefault(room, 0)

def ensure_poll(room):
    poll_state.setdefault(room, {"poll_id": None, "active": False, "responses": {}})

def ensure_hand(room):
    hand_raise_state.setdefault(room, {})

def ensure_voice(room):
    voice_call_state.setdefault(room, {
        "student_socket_id": None,
        "teacher_socket_id": None,
        "student_user_id": None
    })


@socketio.on("join-room")
def join_room_handler(data):
    room = data["class_id"]
    join_room(room)
    ensure_room(room)
    ensure_poll(room)
    ensure_hand(room)
    ensure_voice(room)

    sid_map[request.sid] = {"class_id": room, "user_id": data.get("user_id", request.sid)}

    slide = current_slide[room]
    image = canvas_data[room].get(slide)
    emit("load-canvas", {"image": image, "slide": slide})
    emit("user-joined", {"user_id": request.sid}, room=room, include_self=False)

    poll = poll_state.get(room, {})
    if poll.get("active"):
        emit("poll-start", {
            "poll_id": poll["poll_id"],
            "poll_type": poll["poll_type"],
            "options": poll["options"],
            "timer": poll.get("timer_remaining", poll.get("timer", 30)),
            "question_num": poll.get("question_num", 1)
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

    # Clean up hand raise
    hands = hand_raise_state.get(room, {})
    uid_to_remove = None
    for uid, hinfo in list(hands.items()):
        if hinfo.get("socket_id") == sid:
            uid_to_remove = uid
            break
    if uid_to_remove:
        del hands[uid_to_remove]
        emit("hand-raise", {"user_id": uid_to_remove, "name": "", "raised": False}, room=room)

    # Clean up voice call
    voice = voice_call_state.get(room, {})
    if voice.get("student_socket_id") == sid:
        teacher_sid = voice.get("teacher_socket_id")
        if teacher_sid:
            emit("voice-ended-by-student", {"student_id": voice.get("student_user_id", sid)}, to=teacher_sid)
        voice_call_state[room] = {"student_socket_id": None, "teacher_socket_id": None, "student_user_id": None}
    elif voice.get("teacher_socket_id") == sid:
        student_sid = voice.get("student_socket_id")
        if student_sid:
            emit("voice-end", {"student_id": voice.get("student_user_id")}, to=student_sid)
        voice_call_state[room] = {"student_socket_id": None, "teacher_socket_id": None, "student_user_id": None}


# ── WebRTC teacher camera ──────────────────────────────────────────────────────
@socketio.on("offer")
def offer(data):
    emit("offer", {"offer": data["offer"], "from": request.sid}, to=data["to"])

@socketio.on("answer")
def answer(data):
    emit("answer", {"answer": data["answer"], "from": request.sid}, to=data["to"])

@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {"candidate": data["candidate"], "from": request.sid}, to=data["to"])


# ── Drawing ────────────────────────────────────────────────────────────────────
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


# ── Canvas image ───────────────────────────────────────────────────────────────
@socketio.on("canvas-image")
def canvas_image(data):
    room = data["class_id"]
    slide = data.get("slide", get_slide(room))
    image = data.get("image")
    ensure_room(room)
    canvas_data[room][slide] = image
    emit("canvas-image-update", {"slide": slide, "image": image}, room=room, include_self=False)

@socketio.on("clear-canvas")
def clear_canvas(data):
    room = data["class_id"]
    ensure_room(room)
    canvas_data[room][get_slide(room)] = None
    emit("clear-canvas", {}, room=room, include_self=False)


# ── Slides ─────────────────────────────────────────────────────────────────────
@socketio.on("add-slide")
def add_slide(data):
    room = data["class_id"]
    ensure_room(room)
    new_index = len(canvas_data[room])
    canvas_data[room][new_index] = None
    current_slide[room] = new_index
    emit("slide-changed", {"slide": new_index, "image": None}, room=room)

@socketio.on("add-slide-with-image")
def add_slide_with_image(data):
    room = data["class_id"]
    ensure_room(room)
    new_index = len(canvas_data[room])
    canvas_data[room][new_index] = data.get("image")
    current_slide[room] = new_index
    emit("slide-changed", {"slide": new_index, "image": data.get("image")}, room=room)

@socketio.on("change-slide")
def change_slide(data):
    room = data["class_id"]
    ensure_room(room)
    slide = max(0, min(data["slide"], len(canvas_data[room]) - 1))
    current_slide[room] = slide
    emit("slide-changed", {"slide": slide, "image": canvas_data[room].get(slide)}, room=room)

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
    new_slides = {new: slides[old] for new, old in enumerate(i for i in sorted(slides) if i != idx)}
    canvas_data[room] = new_slides
    cur = max(0, min(current_slide[room], len(new_slides) - 1))
    if current_slide[room] >= idx:
        cur = max(0, cur - 1)
    current_slide[room] = cur
    emit("slide-changed", {"slide": cur, "image": new_slides.get(cur)}, room=room)

@socketio.on("get-slides")
def get_slides(data):
    room = data["class_id"]
    ensure_room(room)
    slides = canvas_data[room]
    emit("slides-list", {"slides": [slides.get(i) for i in sorted(slides)], "current": current_slide.get(room, 0)})


# ── Poll ───────────────────────────────────────────────────────────────────────
@socketio.on("poll-start")
def poll_start(data):
    room = data["class_id"]
    ensure_poll(room)
    poll_state[room] = {
        "poll_id": data["poll_id"], "poll_type": data.get("poll_type"),
        "options": data.get("options", []), "timer": data.get("timer", 30),
        "timer_remaining": data.get("timer", 30), "question_num": data.get("question_num", 1),
        "active": True, "responses": {}
    }
    emit("poll-start", {
        "poll_id": data["poll_id"], "poll_type": data.get("poll_type"),
        "options": data.get("options", []), "timer": data.get("timer", 30),
        "question_num": data.get("question_num", 1)
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
    poll.setdefault("_names", {})[user_id] = name
    emit("poll-response", {
        "poll_id": data["poll_id"], "user_id": user_id,
        "answer": answer, "name": name, "total": len(poll["responses"])
    }, room=room, include_self=True)

@socketio.on("poll-end")
def poll_end(data):
    room = data["class_id"]
    ensure_poll(room)
    poll = poll_state[room]
    poll["active"] = False
    poll["correct"] = data.get("correct")
    merged = {**data.get("responses", {}), **poll.get("responses", {})}
    poll["responses"] = merged
    emit("poll-end", {"poll_id": data["poll_id"], "correct": data.get("correct"), "responses": merged},
         room=room, include_self=False)

@socketio.on("show-leaderboard")
def show_leaderboard(data):
    emit("show-leaderboard", {"leaderboard": data.get("leaderboard", [])},
         room=data["class_id"], include_self=False)


# ── Hand Raise ─────────────────────────────────────────────────────────────────
@socketio.on("hand-raise")
def hand_raise(data):
    room = data["class_id"]
    ensure_hand(room)
    user_id = data.get("user_id") or request.sid
    raised = data.get("raised", True)
    name = data.get("name", "Student")

    if raised:
        # Store the REAL socket SID — this is what makes routing work
        hand_raise_state[room][user_id] = {"name": name, "socket_id": request.sid}
    else:
        hand_raise_state[room].pop(user_id, None)

    emit("hand-raise", {"user_id": user_id, "name": name, "raised": raised},
         room=room, include_self=False)


@socketio.on("hand-dismissed")
def hand_dismissed(data):
    room = data["class_id"]
    user_id = data.get("user_id")
    ensure_hand(room)
    hinfo = hand_raise_state.get(room, {}).pop(user_id, None)
    target_sid = hinfo.get("socket_id") if hinfo else None
    if target_sid:
        emit("hand-dismissed", {"user_id": user_id}, to=target_sid)
    else:
        emit("hand-dismissed", {"user_id": user_id}, room=room, include_self=False)


# ── Voice Call ─────────────────────────────────────────────────────────────────
#
# Flow:
#   1. Teacher clicks Connect  → server sends voice-accept directly to student SID
#   2. Student gets mic        → creates offer → server forwards to teacher SID
#   3. Teacher gets offer      → creates answer → server forwards to student SID
#   4. Both sides trickle ICE  → server routes via target_sid field
#
# All routing uses actual socket SIDs stored in hand_raise_state / voice_call_state.
# No user_id loops needed.

@socketio.on("voice-accept")
def voice_accept(data):
    """Teacher accepts a student's raised hand. data: { class_id, student_id }"""
    room = data["class_id"]
    student_user_id = data.get("student_id")
    ensure_voice(room)

    hinfo = hand_raise_state.get(room, {}).get(student_user_id, {})
    student_sid = hinfo.get("socket_id")

    if not student_sid:
        return  # student gone

    voice_call_state[room] = {
        "student_socket_id": student_sid,
        "teacher_socket_id": request.sid,
        "student_user_id": student_user_id
    }

    # Tell student to start mic and create offer; pass teacher SID so student
    # can use it when sending voice-offer back.
    emit("voice-accept", {
        "student_id": student_user_id,
        "teacher_sid": request.sid
    }, to=student_sid)


@socketio.on("voice-offer")
def voice_offer(data):
    """
    Student sends offer to teacher.
    data: { class_id, teacher_sid, offer }
    We use teacher_sid (sent by student) to route directly.
    We also reply with student's real SID so teacher can send answer back.
    """
    teacher_sid = data.get("teacher_sid")
    if teacher_sid:
        emit("voice-offer", {
            "offer": data["offer"],
            "student_sid": request.sid      # teacher will use this for answer + ICE
        }, to=teacher_sid)


@socketio.on("voice-answer")
def voice_answer(data):
    """
    Teacher sends answer to student.
    data: { student_sid, answer }
    """
    student_sid = data.get("student_sid")
    if student_sid:
        emit("voice-answer", {"answer": data["answer"]}, to=student_sid)


@socketio.on("voice-ice")
def voice_ice(data):
    """
    Trickle ICE — route to target_sid directly.
    data: { target_sid, candidate }
    Both teacher→student and student→teacher use this same event.
    """
    target_sid = data.get("target_sid")
    candidate = data.get("candidate")
    if target_sid and candidate:
        emit("voice-ice", {"candidate": candidate}, to=target_sid)


@socketio.on("voice-end")
def voice_end(data):
    """Teacher ends the call. data: { class_id }"""
    room = data["class_id"]
    ensure_voice(room)
    voice = voice_call_state.get(room, {})
    student_sid = voice.get("student_socket_id")
    student_user_id = voice.get("student_user_id")

    voice_call_state[room] = {"student_socket_id": None, "teacher_socket_id": None, "student_user_id": None}
    hand_raise_state.get(room, {}).pop(student_user_id, None)

    if student_sid:
        emit("voice-end", {}, to=student_sid)

    # Tell everyone to remove from hand queue
    emit("hand-raise", {"user_id": student_user_id, "name": "", "raised": False},
         room=room, include_self=True)


@socketio.on("voice-ended-by-student")
def voice_ended_by_student(data):
    """Student hangs up. data: { class_id, student_id }"""
    room = data["class_id"]
    ensure_voice(room)
    voice = voice_call_state.get(room, {})
    teacher_sid = voice.get("teacher_socket_id")
    student_user_id = data.get("student_id")

    voice_call_state[room] = {"student_socket_id": None, "teacher_socket_id": None, "student_user_id": None}
    hand_raise_state.get(room, {}).pop(student_user_id, None)

    if teacher_sid:
        emit("voice-ended-by-student", {"student_id": student_user_id}, to=teacher_sid)

    emit("hand-raise", {"user_id": student_user_id, "name": "", "raised": False},
         room=room, include_self=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
