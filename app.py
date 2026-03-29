from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from flask_socketio import SocketIO, emit, join_room
import os
import uuid
import cloudinary
import cloudinary.uploader
# ---------------- Base Directory ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates")
)

cloudinary.config(
    cloud_name="dtiy0aqwb",
    api_key="559813745442773",
    api_secret="fkdtSGUU7xaSSXF_D6ybVjx6vmY"
)

socketio = SocketIO(app, cors_allowed_origins="*")

app.secret_key = os.environ.get("SECRET_KEY", "fallback_secret_key")

# ---------------- MongoDB Setup ----------------
MONGO_URI = "mongodb+srv://Vercel-Admin-atlas-claret-kettle:PGhZuRc6LeUN145C@atlas-claret-kettle.mqtmjmc.mongodb.net/?retryWrites=true&w=majority"

if not MONGO_URI:
    raise Exception("MONGO_URI is not set. Add it in environment variables.")

client = MongoClient(MONGO_URI)

try:
    client.admin.command('ping')
    print("✅ MongoDB Connected")
except Exception as e:
    print("❌ MongoDB Error:", e)

db = client["SmartEduDB"]

users_col = db["users"]
classes_col = db["classes"]
user_classes_col = db["user_classes"]
comments_col = db["comments"]
poll_responses_col = db["poll_responses"]
user_courses_col = db["user_courses"]
courses_col = db["courses"]
teachers_col = db["teachers"]
reviews_col = db["reviews"]
followers_col = db["followers"]
notes_col = db["notes"]

# ---------------- Required Decorator ----------------

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("LPfront"))
        return f(*args, **kwargs)
    return decorated

def role_required(required_role):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("LPfront"))
            if session.get("role") != required_role:
                return redirect(url_for("LPfront"))
            return f(*args, **kwargs)
        return decorated
    return decorator

def get_class_datetime(cls):
    return datetime.strptime(
        f"{cls['date']} {cls['time']}",
        "%Y-%m-%d %H:%M"
    )


def get_class_status(cls):
    class_dt = get_class_datetime(cls)
    now = datetime.now()

    diff_minutes = (class_dt - now).total_seconds() / 60

    if diff_minutes > 0:
        return "upcoming"
    elif -60 <= diff_minutes <= 0:
        return "live"
    else:
        return "completed"

# ---------------- Routes ----------------

@app.route("/")
def LPfront():
    if "user_id" in session:
        if session.get("role") == "Teacher":
            return redirect(url_for("LPteachershome"))
        else:
            return redirect(url_for("LPstudenthome"))

    try:
        student_count = users_col.count_documents({"role": "Student"})
        teacher_count = users_col.count_documents({"role": "Teacher"})
        course_count = courses_col.count_documents({})

        # 🔥 GET REVIEWS
        reviews = list(reviews_col.find({}))

        if len(reviews) > 0:
            total_rating = sum(int(r.get("rating", 0)) for r in reviews)
            avg_rating = total_rating / len(reviews)

            success_rate = int((avg_rating / 5) * 100)
        else:
            success_rate = 0   # ❗ IMPORTANT: NOT 95

        print("DEBUG → Reviews:", len(reviews))
        print("DEBUG → Success Rate:", success_rate)

    except Exception as e:
        print("Stats Error:", e)
        student_count = 0
        teacher_count = 0
        course_count = 0
        success_rate = 0

    return render_template(
        "LPfront.html",
        student_count=student_count,
        teacher_count=teacher_count,
        course_count=course_count,
        success_rate=success_rate
    )

@app.route("/LPbookstore") 
def LPbookstore(): 
    return render_template("LPbookstore.html") 

@app.route("/course/<course_id>")
@login_required
def course_page(course_id):
    course = courses_col.find_one({"course_id": course_id})
    return render_template("course_page.html", course=course)


@app.route("/teacher/<user_id>")
@login_required
def teacher_page(user_id):

    # 🔍 Get teacher profile
    teacher = teachers_col.find_one({"user_id": user_id})

    # 🔥 AUTO CREATE PROFILE IF NOT EXISTS
    if not teacher:
        user = users_col.find_one({"id": user_id})

        if not user or user.get("role") != "Teacher":
            return "Teacher not found", 404

        teacher = {
            "teacher_id": str(uuid.uuid4()),
            "user_id": user_id,
            "fullname": user["fullname"],
            "profile_image": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Default_pfp.svg/500px-Default_pfp.svg.png",
            "headline": "",
            "bio": "",
            "education": "",
            "experience": "",
            "languages": [],
            "specialization": "",
            "category": user.get("category", ""),
            "courses": [],
            "free_classes": [],
            "rating": 0,
            "total_students": 0,
            "created_at": datetime.now()
        }

        teachers_col.insert_one(teacher)

    # ✅ COURSES
    courses = list(courses_col.find({
        "teacher_id": teacher["teacher_id"]
    }))

    # ✅ REVIEWS
    reviews = list(reviews_col.find({
        "teacher_id": teacher["teacher_id"]
    }))

    # ✅ TOP FEEDBACK
    top_feedback = ""
    if reviews:
        comments = [r.get("comment") for r in reviews if r.get("comment")]
        if comments:
            top_feedback = comments[0]

    # ✅ RATING BREAKDOWN
    rating_counts = {i: 0 for i in range(1, 6)}

    for r in reviews:
        try:
            rating = int(r.get("rating", 0))
            if rating in rating_counts:
                rating_counts[rating] += 1
        except:
            pass

    total_reviews = len(reviews)

    # 👥 FOLLOWERS COUNT
    followers = followers_col.count_documents({
        "teacher_id": teacher["teacher_id"]
    })

    # 👤 CHECK IF USER IS FOLLOWING
    is_following = followers_col.find_one({
        "follower_id": session["user_id"],
        "teacher_id": teacher["teacher_id"]
    })

    return render_template(
        "teacher_page.html",
        teacher=teacher,
        courses=courses,
        reviews=reviews,
        top_feedback=top_feedback,
        rating_counts=rating_counts,
        total_reviews=total_reviews,

        # 🔥 NEW
        followers=followers,
        is_following=is_following,

        is_owner=(session.get("user_id") == teacher["user_id"])
    )

@app.route("/test/<test_id>")
@login_required
def test_page(test_id):
    test = tests_col.find_one({"test_id": test_id})
    return render_template("test_page.html", test=test)
    
@app.route("/LPteachershome")
@role_required("Teacher")
def LPteachershome():

    user = users_col.find_one({"id": session["user_id"]})

    # ✅ Get teacher courses
    my_courses = list(courses_col.find({
        "teacher_id": session["user_id"]
    }))

    # ✅ Get teacher classes
    my_classes = list(classes_col.find({
        "teacher_id": session["user_id"]
    }))

    return render_template(
        "LPteachershome2.html",
        user=user,
        my_courses=my_courses,
        my_classes=my_classes
    )
    
@app.route("/LPstudenthome")
@role_required("Student")
def LPstudenthome():

    user = users_col.find_one({"id": session["user_id"]})

    # ---------------- USER ENROLLED COURSES ----------------
    user_courses = list(user_courses_col.find({
        "user_id": user["id"]
    }))

    user_course_ids = [uc["course_id"] for uc in user_courses]

    # ✅ ALL COURSES (for Browse)
    all_courses = list(courses_col.find())

    # ---------------- USER CLASSES ----------------
    user_classes = list(user_classes_col.find({
        "user_id": user["id"]
    }))

    class_ids = [uc["class_id"] for uc in user_classes]

    classes = list(classes_col.find({
        "class_id": {"$in": class_ids}
    }))

    # ---------------- PROCESS CLASSES ----------------
    today_classes = []
    upcoming_classes = []

    now = datetime.now()

    teacher_ids = list(set([c.get("teacher_id") for c in classes if c.get("teacher_id")]))

    teachers = list(users_col.find({
        "id": {"$in": teacher_ids}
    }))

    teacher_map = {t["id"]: t.get("fullname", "Unknown") for t in teachers}

    for c in classes:
        class_dt = get_class_datetime(c)

        c["status"] = get_class_status(c)
        c["formatted_time"] = class_dt.strftime("%I:%M %p")
        c["teacher_name"] = teacher_map.get(c.get("teacher_id"), "Unknown")

        if class_dt.date() == now.date():
            today_classes.append(c)
        elif class_dt > now:
            upcoming_classes.append(c)

    today_classes = sorted(today_classes, key=lambda x: x["time"])
    upcoming_classes = sorted(upcoming_classes, key=lambda x: (x["date"], x["time"]))

    # ---------------- ENROLLED COURSES FOR HOME ----------------
    enrolled_courses = list(courses_col.find({
        "course_id": {"$in": user_course_ids}
    }))

    return render_template(
        "LPstudenthome.html",
        user=user,

        # ✅ FIXED
        courses=enrolled_courses,        # Home tab
        all_courses=all_courses,        # Browse tab

        today_classes=today_classes,
        upcoming_classes=upcoming_classes,
        user_courses_ids=user_course_ids
    )
    
@app.route("/LPcourse") 
def LPcourse(): 
    return render_template("LPcourse.html") 
    
@app.route("/LPregisteryourself") 
def LPregisteryourself(): 
    return render_template("LPregisteryourself.html") 
    
@app.route("/LPliveclasses") 
@login_required 
def LPliveclasses(): 
    return render_template("LPliveclasses.html")

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "GET":
        return render_template("LPregisteryourself.html")

    try:
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        role = request.form.get("role")
        phone = request.form.get("phone")

        # ✅ Validation
        if not all([fullname, email, password, confirm_password, role, phone]):
            return render_template("LPregisteryourself.html", error="All fields are required")

        if password != confirm_password:
            return render_template("LPregisteryourself.html", error="Passwords do not match")

        existing_user = users_col.find_one({"email": email})
        if existing_user:
            return render_template("LPregisteryourself.html", error="User already exists")

        # ✅ Create user_id first
        user_id = str(uuid.uuid4())

        hashed_password = generate_password_hash(password)

        # ✅ Insert user
        users_col.insert_one({
            "id": user_id,
            "fullname": fullname,
            "email": email,
            "password": hashed_password,
            "role": role,
            "phone": phone,
            "created_at": datetime.now(timezone.utc),
            "subscribed": "no",
            "enrolled_course": "none",
            "subcription_till": "00/00/0000"
        })

        # ✅ If teacher → create public profile
        if role == "Teacher":
            teachers_col.insert_one({
                "teacher_id": str(uuid.uuid4()),
                "user_id": user_id,  # 🔗 link to users table

                "fullname": fullname,
                "profile_image": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Default_pfp.svg/500px-Default_pfp.svg.png?_=20220226140232",

                "headline": "",
                "bio": "",
                "education": "",
                "experience": "",
                "languages": [],

                "specialization": "",
                "category": "",

                "courses": [],
                "free_classes": [],

                "rating": 0,
                "total_students": 0,

                "created_at": datetime.now(timezone.utc)
            })

        return redirect(url_for("LPfront"))

    except Exception as e:
        return render_template("LPregisteryourself.html", error=str(e))

# ---------------- LOGIN ----------------
@app.route("/login", methods=["POST"])
def login():
    try:
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("LPfront.html", error="Missing email or password")

        user = users_col.find_one({"email": email})

        if not user:
            return render_template("LPfront.html", error="User not found")

        if not check_password_hash(user["password"], password):
            return render_template("LPfront.html", error="Incorrect password")

        session["user_id"] = user["id"]
        session["role"] = user["role"]

        if user["role"] == "Teacher":
            return redirect(url_for("LPteachershome"))
        else:
            return redirect(url_for("LPstudenthome"))

    except Exception as e:
        return render_template("LPfront.html", error=str(e))

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("LPfront"))

# ------ Create Course ---------
@app.route("/create_course", methods=["POST"])
@role_required("Teacher")
def create_course():
    try:
        name = request.form.get("name")
        desc = request.form.get("desc")
        total_classes = request.form.get("total_classes")
        category = request.form.get("category")
        time = request.form.get("time")
        start_date_str = request.form.get("start_date")

        if not name or not total_classes or not category or not time or not start_date_str:
            return jsonify({"error": "Missing fields"}), 400

        try:
            total_classes = int(total_classes)
        except:
            return jsonify({"error": "Invalid number of classes"}), 400

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except:
            return jsonify({"error": "Invalid start date"}), 400

        try:
            datetime.strptime(time, "%H:%M")
        except:
            return jsonify({"error": "Invalid time format"}), 400

        teacher_id = session.get("user_id")
        if not teacher_id:
            return jsonify({"error": "Not logged in"}), 401

        course_id = str(uuid.uuid4())

        courses_col.insert_one({
            "course_id": course_id,
            "name": name,
            "desc": desc,
            "category": category,
            "teacher_id": teacher_id,
            "total_classes": total_classes,
            "start_date": start_date,
            "created_at": datetime.now(timezone.utc)
        })

        # ✅ FIXED INDENTATION
        teachers_col.update_one(
            {"user_id": teacher_id},
            {"$push": {"courses": course_id}}
        )

        current_date = start_date

        for _ in range(total_classes):
            class_date = current_date.strftime("%Y-%m-%d")

            classes_col.insert_one({
                "class_id": str(uuid.uuid4()),
                "course_id": course_id,
                "teacher_id": teacher_id,
                "subject": name,
                "category": category,
                "date": class_date,
                "time": time,
                "status": "upcoming"
            })

            current_date += timedelta(days=1)

        return jsonify({
            "message": "Course created successfully 🚀",
            "course_id": course_id
        })

    except Exception as e:
        print("CREATE COURSE ERROR:", e)
        return jsonify({"error": "Internal server error"}), 500

# ---- enroll -------
@app.route("/enroll/<course_id>")
@login_required
def enroll(course_id):

    user_id = session["user_id"]

    # 🔒 Prevent duplicate enrollment
    existing = db.user_courses.find_one({
        "user_id": user_id,
        "course_id": course_id
    })

    if existing:
        return redirect(url_for("LPstudenthome"))

    db.user_courses.insert_one({
        "user_id": user_id,
        "course_id": course_id
    })

    # Auto-enroll in classes
    classes = db.classes.find({"course_id": course_id})

    for c in classes:
        db.user_classes.insert_one({
            "user_id": user_id,
            "class_id": c["class_id"]
        })

    return redirect(url_for("LPstudenthome"))

# ---- TEacher catagory -----

@app.route("/set_teacher_category", methods=["POST"])
@role_required("Teacher")
def set_teacher_category():

    try:
        category = request.form.get("category")
        print("CATEGORY:", category)

        users_col.update_one(
            {"id": session["user_id"]},
            {"$set": {"category": category}}
        )

        return jsonify({"message": "Category saved"})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"error": str(e)}), 500

#--profile image 

@app.route("/upload-profile-image", methods=["POST"])
@role_required("Teacher")
def upload_profile_image():

    if "image" not in request.files:
        return "No file", 400

    file = request.files["image"]

    if file.filename == "":
        return "No selected file", 400

    # 🔥 get old image
    teacher = teachers_col.find_one({"user_id": session["user_id"]})

    if teacher and teacher.get("profile_image_id"):
        try:
            cloudinary.uploader.destroy(teacher["profile_image_id"])
        except Exception as e:
            print("Delete failed:", e)

    # 🔥 upload new image
    result = cloudinary.uploader.upload(
        file,
        folder="profile_pics",
        transformation=[
            {"width": 300, "height": 300, "crop": "fill", "gravity": "face"}
        ]
    )

    image_url = result.get("secure_url")
    public_id = result.get("public_id")

    # 🔥 update DB
    teachers_col.update_one(
        {"user_id": session["user_id"]},
        {
            "$set": {
                "profile_image": image_url,
                "profile_image_id": public_id
            }
        }
    )

    return redirect(url_for("LPteachershome"))


#reviewwww for teachers 

@app.route("/add-review/<teacher_id>", methods=["POST"])
@login_required
def add_review(teacher_id):

    user_id = session["user_id"]
    rating = int(request.form.get("rating"))
    comment = request.form.get("comment")

    teacher = teachers_col.find_one({"teacher_id": teacher_id})

    if not teacher:
        return "Teacher not found", 404

    # ❌ prevent self review
    if teacher["user_id"] == user_id:
        return "You cannot review yourself", 400

    # 🔥 CHECK IF REVIEW EXISTS
    existing = reviews_col.find_one({
        "teacher_id": teacher_id,
        "user_id": user_id
    })

    if existing:
        # ✅ UPDATE REVIEW
        reviews_col.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "rating": rating,
                    "comment": comment,
                    "updated_at": datetime.now()
                }
            }
        )
    else:
        # ✅ INSERT NEW REVIEW
        reviews_col.insert_one({
            "teacher_id": teacher_id,
            "user_id": user_id,
            "rating": rating,
            "comment": comment,
            "created_at": datetime.now()
        })

    # 🔥 RECALCULATE RATING
    reviews = list(reviews_col.find({"teacher_id": teacher_id}))
    avg = sum(r["rating"] for r in reviews) / len(reviews)

    teachers_col.update_one(
        {"teacher_id": teacher_id},
        {
            "$set": {
                "rating": round(avg, 1),
                "total_students": len(reviews)
            }
        }
    )

    return redirect(url_for("teacher_page", user_id=teacher["user_id"]))


#-------- GET class---------
@app.route("/get-classes")
@role_required("Student")
def get_classes_by_date():

    date = request.args.get("date")
    user_id = session["user_id"]

    # user classes
    user_classes = list(user_classes_col.find({
        "user_id": user_id
    }))

    class_ids = [uc["class_id"] for uc in user_classes]

    classes = list(classes_col.find({
        "class_id": {"$in": class_ids},
        "date": date
    }))

    # 🔥 get all teacher_ids first (optimized)
    teacher_ids = list(set([c.get("teacher_id") for c in classes if c.get("teacher_id")]))

    teachers = list(users_col.find({
        "id": {"$in": teacher_ids}
    }))

    # map id → name
    teacher_map = {t["id"]: t.get("fullname", "Unknown") for t in teachers}

    result = []

    for c in classes:
        teacher_name = teacher_map.get(c.get("teacher_id"), "Unknown")

        result.append({
            "subject": c.get("subject", ""),
            "time": c.get("time", ""),
            "teacher_name": teacher_name
        })

    return jsonify(result)

#------ subscribeee --------

@app.route("/subscribe", methods=["POST"])
@login_required
def subscribe():

    user_id = session["user_id"]
    plan = int(request.form.get("plan"))

    # calculate expiry date
    today = datetime.now()
    expiry_date = today + timedelta(days=30 * plan)

    formatted_date = expiry_date.strftime("%d/%m/%Y")

    # update DB
    users_col.update_one(
        {"id": user_id},
        {
            "$set": {
                "subscribed": "yes",
                "subcription_till": formatted_date
            }
        }
    )

    return redirect(url_for("LPstudenthome"))


#------ searchhh kar leee-----
@app.route("/search")
@login_required
def search():
    try:
        query = request.args.get("q", "").strip().lower()

        if not query:
            return jsonify([])

        regex = {"$regex": query, "$options": "i"}

        results = []

        # 🔍 Courses
        courses = list(courses_col.find({"name": regex}))
        for c in courses:
            name = c.get("name", "")
            score = 2 if name.lower().startswith(query) else 1

            results.append({
                "type": "course",
                "name": name,
                "id": c.get("course_id"),
                "score": score
            })

        # 🔍 Teachers
        teachers = list(users_col.find({
            "fullname": regex,
            "role": "Teacher"
        }))
        for t in teachers:
            name = t.get("fullname", "")
            score = 2 if name.lower().startswith(query) else 1

            results.append({
                "type": "teacher",
                "name": name,
                "id": t.get("id"),
                "score": score
            })

        # 🔍 Tests
        try:
            tests = list(tests_col.find({"name": regex}))
            for t in tests:
                name = t.get("name", "")
                score = 2 if name.lower().startswith(query) else 1

                results.append({
                    "type": "test",
                    "name": name,
                    "id": t.get("test_id"),
                    "score": score
                })
        except:
            pass

        # 🔥 Sort by score (exact match first)
        results = sorted(results, key=lambda x: x["score"], reverse=True)

        return jsonify(results[:5])

    except Exception as e:
        print("SEARCH ERROR:", e)
        return jsonify([])

@app.route("/search-page")
@login_required
def search_page():

    query = request.args.get("q", "")

    regex = {"$regex": query, "$options": "i"}

    courses = list(courses_col.find({"name": regex}))
    teachers = list(users_col.find({"fullname": regex, "role": "Teacher"}))
    tests = list(classes_col.find({"subject": regex}))

    return render_template(
        "search_results.html",
        courses=courses,
        teachers=teachers,
        tests=tests,
        query=query
    )


@app.route("/update-teacher/<teacher_id>", methods=["POST"])
@login_required
def update_teacher(teacher_id):

    teacher = teachers_col.find_one({"teacher_id": teacher_id})

    if teacher["user_id"] != session["user_id"]:
        return "Unauthorized", 403

    teachers_col.update_one(
        {"teacher_id": teacher_id},
        {
            "$set": {
                "headline": request.form.get("headline"),
                "education": request.form.get("education"),
                "experience": request.form.get("experience"),
                "bio": request.form.get("bio"),
                "languages": request.form.get("languages").split(",")
            }
        }
    )

    return redirect(url_for("teacher_page", user_id=session["user_id"]))

@app.route("/toggle-follow/<teacher_id>", methods=["POST"])
@login_required
def toggle_follow(teacher_id):

    user_id = session["user_id"]

    # ❌ prevent self follow
    teacher = teachers_col.find_one({"teacher_id": teacher_id})
    if teacher["user_id"] == user_id:
        return jsonify({"status": "error"})

    existing = followers_col.find_one({
        "follower_id": user_id,
        "teacher_id": teacher_id
    })

    if existing:
        followers_col.delete_one({"_id": existing["_id"]})
        return jsonify({"status": "unfollowed"})
    else:
        followers_col.insert_one({
            "follower_id": user_id,
            "teacher_id": teacher_id
        })
        return jsonify({"status": "followed"})


@app.route("/upload-note", methods=["POST"])
@role_required("Teacher")
def upload_note():

    try:
        title = request.form.get("title")
        course_id = request.form.get("course_id")

        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400

        file = request.files["file"]

        result = cloudinary.uploader.upload(
            file,
            resource_type="auto",
            folder="notes"
        )

        file_url = result.get("secure_url")

        notes_col.insert_one({
            "note_id": str(uuid.uuid4()),
            "title": title,
            "file_url": file_url,
            "course_id": course_id,
            "teacher_id": session["user_id"],
            "created_at": datetime.now()
        })

        return jsonify({"message": "Uploaded successfully ✅"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/teacher-course/<course_id>")
def teacher_course_page(course_id):

    course = courses_col.find_one({"course_id": course_id})

    classes = list(classes_col.find({
        "course_id": course_id
    }))

    notes = list(notes_col.find({
        "course_id": course_id   # 🔥 IMPORTANT
    }))

    return render_template(
        "teacher_course.html",
        course=course,
        classes=classes,
        notes=notes
    )


@app.route("/teacher_class/<class_id>")
@role_required("Teacher")
def teacher_class_page(class_id):

    cls = classes_col.find_one({"class_id": class_id})

    # ❌ INVALID
    if not cls:
        return render_template(
            "teacher_class.html",
            invalid=True,
            cls=None
        )

    # 🔒 SECURITY: only that teacher can open
    if cls.get("teacher_id") != session["user_id"]:
        return "Unauthorized", 403

    teacher = users_col.find_one({"id": session["user_id"]})
    teacher_name = teacher.get("fullname", "Teacher")

    return render_template(
        "teacher_class.html",
        cls=cls,
        teacher_name=teacher_name,
        invalid=False
    )

@app.route("/update-class", methods=["POST"])
@role_required("Teacher")
def update_class():

    data = request.json

    classes_col.update_one(
        {"class_id": data["class_id"]},
        {
            "$set": {
                "subject": data["subject"],
                "date": data["date"],
                "time": data["time"]
            }
        }
    )

    return jsonify({"message": "updated"})


@app.route("/delete-class/<class_id>", methods=["DELETE"])
@role_required("Teacher")
def delete_class(class_id):

    classes_col.delete_one({"class_id": class_id})

    return jsonify({"message": "deleted"})


@app.route("/add-class", methods=["POST"])
def add_class():
    data = request.json

    classes_col.insert_one({
        "class_id": str(uuid.uuid4()),
        "course_id": data["course_id"],
        "subject": data["subject"],
        "date": data["date"],
        "time": data["time"],
        "status": "upcoming"
    })

    return jsonify({"message":"added"})


@app.route("/cancel-class/<class_id>", methods=["POST"])
def cancel_class(class_id):

    classes_col.update_one(
        {"class_id": class_id},
        {"$set": {"status": "cancelled"}}
    )

    return jsonify({"message":"cancelled"})

@app.route("/update-note", methods=["POST"])
def update_note():

    data = request.json

    notes_col.update_one(
        {"note_id": data["note_id"]},
        {"$set": {"title": data["title"]}}
    )

    return jsonify({"message":"updated"})

@app.route("/delete-note/<note_id>", methods=["DELETE"])
def delete_note(note_id):

    notes_col.delete_one({"note_id": note_id})

    return jsonify({"message":"deleted"})


@app.route("/student_class/<class_id>")
@login_required
def student_class_page(class_id):

    cls = classes_col.find_one({"class_id": class_id})

    # ❌ INVALID CLASS
    if not cls:
        return render_template(
            "student_class.html",
            invalid=True,
            cls=None
        )

    # ---------------- TEACHER ----------------
    teacher = users_col.find_one({"id": cls.get("teacher_id")})
    teacher_name = teacher["fullname"] if teacher else "Unknown"

    # ---------------- TIME LOGIC ----------------
    class_dt = get_class_datetime(cls)
    now = datetime.now()

    diff_minutes = (class_dt - now).total_seconds() / 60

    # ---------------- STATUS ----------------
    if diff_minutes > 0:
        status = "upcoming"
    elif -60 <= diff_minutes <= 0:
        status = "live"
    else:
        status = "completed"

    # ---------------- FLAGS ----------------
    is_live = status == "live"
    can_join = -10 <= diff_minutes <= 60

    return render_template(
        "student_class.html",
        cls=cls,
        teacher_name=teacher_name,
        status=status,
        is_live=is_live,
        can_join=can_join,
        invalid=False
    )

@app.route("/join-class", methods=["POST"])
@login_required
def join_class():

    data = request.json
    class_id = data.get("class_id")

    user = users_col.find_one({"id": session["user_id"]})

    if not class_id:
        return jsonify({"error": "Missing class_id"}), 400

    # 🔥 prevent duplicate join spam
    last_join = comments_col.find_one(
        {
            "class_id": class_id,
            "user_id": session["user_id"],
            "type": "join"
        },
        sort=[("created_at", -1)]
    )

    if last_join:
        diff = (datetime.now() - last_join["created_at"]).total_seconds()
        if diff < 30:
            return jsonify({"message": "already joined recently"})

    comments_col.insert_one({
        "comment_id": str(uuid.uuid4()),
        "class_id": class_id,
        "user_id": session["user_id"],
        "name": user.get("fullname"),
        "role": user.get("role"),
        "type": "join",  # 🔥 IMPORTANT
        "created_at": datetime.now()
    })

    return jsonify({"message": "joined"})


# ---------------- ADD COMMENT ----------------
@app.route("/add-comment", methods=["POST"])
@login_required
def add_comment():

    data = request.json
    class_id = data.get("class_id")
    text = data.get("text")

    if not class_id or not text:
        return jsonify({"error": "Missing data"}), 400

    user = users_col.find_one({"id": session["user_id"]})

    comment = {
    "comment_id": str(uuid.uuid4()),
    "class_id": class_id,
    "user_id": session["user_id"],
    "name": user.get("fullname"),
    "role": user.get("role"),
    "text": text,
    "type": "message",  # 🔥 ADD THIS
    "created_at": datetime.now()
    }
    
    comments_col.insert_one(comment)

    return jsonify({"message": "sent"})


# ---------------- GET COMMENTS ----------------
@app.route("/get-comments/<class_id>")
@login_required
def get_comments(class_id):

    comments = list(comments_col.find(
        {"class_id": class_id}
    ).sort("created_at", 1))

    result = []

    join_buffer = []

    def flush_join_buffer():
        if not join_buffer:
            return

        if len(join_buffer) == 1:
            result.append({
                "type": "join",
                "text": f"{join_buffer[0]['name']} joined",
                "time": join_buffer[0]["created_at"].strftime("%I:%M %p")
            })
        else:
            first = join_buffer[0]["name"]
            count = len(join_buffer) - 1

            result.append({
                "type": "join",
                "text": f"{first} and {count} others joined",
                "time": join_buffer[-1]["created_at"].strftime("%I:%M %p")
            })

        join_buffer.clear()

    for c in comments:

        if c.get("type") == "join":
            join_buffer.append(c)

        else:
            # 🔥 flush joins before message
            flush_join_buffer()

            result.append({
                "type": "message",
                "name": c.get("name"),
                "role": c.get("role"),
                "text": c.get("text"),
                "time": c.get("created_at").strftime("%I:%M %p")
            })

    # 🔥 flush remaining joins
    flush_join_buffer()

    return jsonify(result)


@socketio.on("join-room")
def join_class(data):
    class_id = data["class_id"]

    join_room(class_id)

    emit("user-joined", {
        "user_id": request.sid
    }, to=class_id, include_self=False)


@socketio.on("offer")
def handle_offer(data):
    emit("offer", {
        "offer": data["offer"],
        "from": request.sid
    }, to=data["to"])


@socketio.on("answer")
def handle_answer(data):
    emit("answer", {
        "answer": data["answer"],
        "from": request.sid
    }, to=data["to"])


@socketio.on("ice-candidate")
def handle_ice(data):
    emit("ice-candidate", {
        "candidate": data["candidate"],
        "from": request.sid
    }, to=data["to"])



# ---------------- HEALTH CHECK ----------------
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# ---------------- Run ----------------
if __name__ == "__main__":
    socketio.run(app, debug=True)
