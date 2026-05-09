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
    async_mode="gevent",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=50 * 1024 * 1024  # 50MB for large PDF/image uploads
)

# { class_id: { slide_index: image_dataURL_or_None } }
canvas_data = {}

# { class_id: current_slide_index }
current_slide = {}

# Poll state per class
poll_state = {}

# Hand raise state per class
hand_raise_state = {}

# Voice call state per class
voice_call_state = {}

# Map socket_id → { class_id, user_id } for disconnect cleanup
sid_map = {}


@app.route("/")
def home():
    return "SmartEdu Socket Server ✅"


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

def ensure_hand(room):
    hand_raise_state.setdefault(room, {})

def ensure_voice(room):
    voice_call_state.setdefault(room, {
        "student_id": None,
        "teacher_socket_id": None
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
    ensure_hand(room)
    ensure_voice(room)

    sid_map[request.sid] = {
        "class_id": room,
        "user_id": data.get("user_id", request.sid)
    }

    slide = current_slide[room]
    image = canvas_data[room].get(slide)

    emit("load-canvas", {"image": image, "slide": slide})

    emit("user-joined", {"user_id": request.sid}, room=room, include_self=False)

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

    # Re-send current hand raise state to teacher
    current_hands = hand_raise_state.get(room, {})
    for uid, info in current_hands.items():
        emit("hand-raise", {
            "user_id": uid,
            "name": info.get("name", "Student"),
            "raised": True
        }, room=room, include_self=True)


# =========================
# DISCONNECT CLEANUP
# =========================
@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    info = sid_map.pop(sid, None)
    if not info:
        return

    room = info.get("class_id")
    if not room:
        return

    # Clean up hand raise if this student had their hand up
    hands = hand_raise_state.get(room, {})
    uid_to_remove = None
    for uid, hinfo in list(hands.items()):
        if hinfo.get("socket_id") == sid:
            uid_to_remove = uid
            break
    if uid_to_remove:
        del hands[uid_to_remove]
        emit("hand-raise", {
            "user_id": uid_to_remove,
            "name": "",
            "raised": False
        }, room=room)

    # Clean up voice call if this student was in a call
    voice = voice_call_state.get(room, {})
    if voice.get("student_id") == info.get("user_id") or voice.get("student_socket") == sid:
        voice["student_id"] = None
        voice["teacher_socket_id"] = None
        emit("voice-ended-by-student", {
            "student_id": info.get("user_id", sid)
        }, room=room)


# =========================
# WEBRTC SIGNALING (teacher video → students)
# =========================
@socketio.on("offer")
def offer(data):
    emit("offer", {"offer": data["offer"], "from": request.sid}, to=data["to"])


@socketio.on("answer")
def answer(data):
    emit("answer", {"answer": data["answer"], "from": request.sid}, to=data["to"])


@socketio.on("ice-candidate")
def ice(data):
    emit("ice-candidate", {"candidate": data["candidate"], "from": request.sid}, to=data["to"])


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
# UPLOAD PROGRESS (teacher → students)
# FIX: relay teacher upload progress to all students
# =========================
@socketio.on("upload-start")
def upload_start(data):
    room = data["class_id"]
    emit("upload-start", {
        "label": data.get("label", "Loading content…")
    }, room=room, include_self=False)


@socketio.on("upload-progress")
def upload_progress(data):
    room = data["class_id"]
    emit("upload-progress", {
        "label": data.get("label", "Loading…"),
        "pct": data.get("pct", 0)
    }, room=room, include_self=False)


# =========================
# GET SLIDES FOR PDF (HTTP endpoint)
# FIX: add this endpoint so students can download slides after class ends
# =========================
@app.route("/get-slides-pdf/<class_id>")
def get_slides_pdf(class_id):
    from flask import jsonify
    ensure_room(class_id)
    slides = canvas_data.get(class_id, {})
    ordered = [slides.get(i) for i in sorted(slides.keys())]
    # Filter out None slides
    valid = [s for s in ordered if s]
    return jsonify({"slides": valid, "total": len(valid)})


# =========================
# CLASS ENDED
# FIX: teacher broadcasts class-end to all students
# =========================
@socketio.on("class-ended")
def class_ended(data):
    room = data["class_id"]
    # Broadcast to all students in the room (not the teacher)
    emit("class-ended", {}, room=room, include_self=False)


# =========================
# POLL SYSTEM
# =========================
@socketio.on("poll-start")
def poll_start(data):
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
    emit("poll-start", {
        "poll_id": data["poll_id"],
        "poll_type": data.get("poll_type"),
        "options": data.get("options", []),
        "timer": data.get("timer", 30),
        "question_num": data.get("question_num", 1)
    }, room=room, include_self=False)


@socketio.on("poll-response")
def poll_response(data):
    room = data["class_id"]
    ensure_poll(room)
    poll = poll_state[room]
    if not poll.get("active"):
        return
    if poll.get("poll_id") != data.get("poll_id"):
        return
    user_id = data.get("user_id") or request.sid
    answer = data.get("answer")
    name = data.get("name", "Student")
    poll["responses"][user_id] = answer
    if "_names" not in poll:
        poll["_names"] = {}
    poll["_names"][user_id] = name
    emit("poll-response", {
        "poll_id": data["poll_id"],
        "user_id": user_id,
        "answer": answer,
        "name": name,
        "total": len(poll["responses"])
    }, room=room, include_self=True)


@socketio.on("poll-end")
def poll_end(data):
    room = data["class_id"]
    ensure_poll(room)
    poll = poll_state[room]
    poll["active"] = False
    poll["correct"] = data.get("correct")
    server_responses = poll.get("responses", {})
    client_responses = data.get("responses", {})
    merged = {**client_responses, **server_responses}
    poll["responses"] = merged
    emit("poll-end", {
        "poll_id": data["poll_id"],
        "correct": data.get("correct"),
        "responses": merged
    }, room=room, include_self=False)


@socketio.on("show-leaderboard")
def show_leaderboard(data):
    room = data["class_id"]
    emit("show-leaderboard", {
        "leaderboard": data.get("leaderboard", [])
    }, room=room, include_self=False)


# =========================
# HAND RAISE SYSTEM
# =========================
@socketio.on("hand-raise")
def hand_raise(data):
    room = data["class_id"]
    ensure_hand(room)
    user_id = data.get("user_id") or request.sid
    raised = data.get("raised", True)
    name = data.get("name", "Student")

    if raised:
        hand_raise_state[room][user_id] = {
            "name": name,
            "socket_id": request.sid,
            "raised_at": data.get("raised_at", 0)
        }
    else:
        hand_raise_state[room].pop(user_id, None)

    emit("hand-raise", {
        "user_id": user_id,
        "name": name,
        "raised": raised
    }, room=room, include_self=False)


@socketio.on("hand-dismissed")
def hand_dismissed(data):
    room = data["class_id"]
    user_id = data.get("user_id")
    ensure_hand(room)

    student_info = hand_raise_state.get(room, {}).get(user_id)
    student_sid = None
    if student_info:
        student_sid = student_info.get("socket_id")

    # Remove from server-side queue AFTER getting socket_id
    hand_raise_state[room].pop(user_id, None)

    if student_sid:
        emit("hand-dismissed", {"user_id": user_id}, to=student_sid)
    else:
        emit("hand-dismissed", {"user_id": user_id}, room=room, include_self=False)


# =========================
# VOICE CALL SYSTEM
# FIX: proper relay and ICE candidate routing
# =========================
@socketio.on("voice-accept")
def voice_accept(data):
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    voice_call_state[room] = {
        "student_id": student_id,
        "teacher_socket_id": request.sid
    }

    # Find student socket id from hand raise state
    student_socket_id = None
    for uid, info in hand_raise_state.get(room, {}).items():
        if uid == student_id:
            student_socket_id = info.get("socket_id")
            break

    payload = {
        "student_id": student_id,
        "teacher_socket": request.sid
    }

    if student_socket_id:
        emit("voice-accept", payload, to=student_socket_id)
    else:
        # Fallback: broadcast to room
        emit("voice-accept", payload, room=room, include_self=False)


@socketio.on("voice-offer")
def voice_offer(data):
    room = data["class_id"]
    voice = voice_call_state.get(room, {})
    teacher_sid = voice.get("teacher_socket_id")

    if teacher_sid:
        emit("voice-offer", {
            "student_id": data.get("student_id"),
            "offer": data["offer"]
        }, to=teacher_sid)


@socketio.on("voice-answer")
def voice_answer(data):
    room = data["class_id"]
    student_id = data.get("student_id")

    # Find student socket from hand raise state
    student_socket_id = None
    for uid, info in hand_raise_state.get(room, {}).items():
        if uid == student_id:
            student_socket_id = info.get("socket_id")
            break

    if student_socket_id:
        emit("voice-answer", {
            "student_id": student_id,
            "answer": data["answer"]
        }, to=student_socket_id)


@socketio.on("voice-ice")
def voice_ice(data):
    """
    ICE candidate relay for voice call (bidirectional).
    FIX: properly route ICE candidates both ways using TURN relay
    """
    room = data["class_id"]
    candidate = data.get("candidate")
    from_teacher = data.get("from_teacher", False)
    student_id = data.get("student_id")

    if from_teacher:
        # Teacher → student
        student_socket_id = None
        for uid, info in hand_raise_state.get(room, {}).items():
            if uid == student_id:
                student_socket_id = info.get("socket_id")
                break
        if student_socket_id:
            emit("voice-ice-student", {
                "candidate": candidate,
                "student_id": student_id
            }, to=student_socket_id)
    else:
        # Student → teacher
        voice = voice_call_state.get(room, {})
        teacher_sid = voice.get("teacher_socket_id")
        if teacher_sid:
            emit("voice-ice-teacher", {
                "candidate": candidate,
                "student_id": student_id
            }, to=teacher_sid)


@socketio.on("voice-end")
def voice_end(data):
    """Teacher ends voice call."""
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    voice = voice_call_state.get(room, {})
    voice_call_state[room] = {"student_id": None, "teacher_socket_id": None}

    # Find student socket and notify
    student_socket_id = None
    for uid, info in hand_raise_state.get(room, {}).items():
        if uid == student_id:
            student_socket_id = info.get("socket_id")
            break

    # Remove from hand queue
    hand_raise_state.get(room, {}).pop(student_id, None)

    if student_socket_id:
        emit("voice-end", {"student_id": student_id}, to=student_socket_id)
    else:
        emit("voice-end", {"student_id": student_id}, room=room, include_self=False)

    # Update everyone's hand queue
    emit("hand-raise", {
        "user_id": student_id,
        "name": "",
        "raised": False
    }, room=room, include_self=True)


@socketio.on("voice-ended-by-student")
def voice_ended_by_student(data):
    """Student ends voice call."""
    room = data["class_id"]
    student_id = data.get("student_id")
    ensure_voice(room)

    voice = voice_call_state.get(room, {})
    teacher_sid = voice.get("teacher_socket_id")

    voice_call_state[room] = {"student_id": None, "teacher_socket_id": None}
    hand_raise_state.get(room, {}).pop(student_id, None)

    if teacher_sid:
        emit("voice-ended-by-student", {"student_id": student_id}, to=teacher_sid)

    emit("hand-raise", {
        "user_id": student_id,
        "name": "",
        "raised": False
    }, room=room, include_self=False)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
