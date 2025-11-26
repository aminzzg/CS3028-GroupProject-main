from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import os
from sqlalchemy import create_engine, text
from functools import wraps
import datetime

views = Blueprint('views', __name__)

# ---- DB connection ----
engine = create_engine(
    "mysql+pymysql://sql8806914:eL7S6etubu@sql8.freesqldatabase.com:3306/sql8806914",
    pool_pre_ping=True
)

def require_admin():
    if session.get("role") != "Admin":
        flash("Admin access required.")
        return False
    return True

# ------------------- LOGIN -------------------
@views.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT StaffID, Name, Username, Password, Position
                    FROM Staff
                    WHERE Username = :u AND Password = :p
                """),
                {"u": username, "p": password}
            ).mappings().fetchone()

        if row:
            session['staff_id'] = row['StaffID']  # Now valid
            session['username'] = row['Username']
            session['name'] = row['Name']
            session['role'] = row['Position']

            # Redirect EVERYONE to notifications
            return redirect(url_for('views.notifications'))

        else:
            flash("Invalid username or password.")
    return render_template('login.html')

# ------------------- ADMIN DASHBOARD -------------------
@views.route('/admin-dashboard')
def admin_dashboard():
    if session.get('role') != 'Admin':
        return redirect(url_for('auth.login'))
    return render_template('admin.html', name=session.get('name'), role=session.get('role'))


# ------------------- STAFF DASHBOARD -------------------
@views.route('/staff-dashboard')
def staff_dashboard():
    if session.get('role') != 'Staff':
        return redirect(url_for('auth.login'))
    return render_template('staff.html', name=session.get('name'), role=session.get('role'))


# ------------------- LOGOUT -------------------
@views.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    flash("Logged out successfully.")
    return redirect(url_for('auth.login'))


# ------------------- MANAGER DASHBOARD (single route) -------------------
@views.route('/manager')
def manager_dashboard():
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    search_query = request.args.get('search', '', type=str)

    with engine.begin() as conn:
        # COURSES:
        # - Use real columns from your screenshot
        # - Alias ClassInfo AS CourseInfo so your template can keep {{ course.CourseInfo }}
        # - Compute AllocatedStaff by counting Allocations per course
        courses = conn.execute(
    text("""
        SELECT 
            c.CourseID,
            c.CourseName,
            c.ClassSize,
            c.CourseHead,
            c.ClassInfo AS CourseInfo,
            COALESCE(COUNT(DISTINCT a.StaffID), 0) AS AllocatedStaff
        FROM Courses c
        LEFT JOIN Allocations a ON a.CourseID = c.CourseID
        WHERE (:q = '' OR c.CourseName LIKE :likeq)
        GROUP BY c.CourseID, c.CourseName, c.ClassSize, c.CourseHead, c.ClassInfo
        ORDER BY c.CourseName
    """),
    {"q": search_query, "likeq": f"%{search_query}%"}
).mappings().all()


        # ALLOCATIONS (for the Allocations tab)
        allocations = conn.execute(
            text("""
                SELECT 
                    AllocationID,
                    StaffID,
                    CourseID,
                    AllocationDate,
                    AssignedHour,
                    TotalHours
                FROM Allocations
                ORDER BY AllocationDate DESC
            """)
        ).mappings().all()

        # STAFF (optional, for Staff Management tab)
        staff = conn.execute(
            text("""
                SELECT 
                    StaffID,
                    Name,
                    Position
                FROM Staff
                ORDER BY Name
            """)
        ).mappings().all()

    return render_template(
        'manager.html',
        name=session.get('name'),
        role=session.get('role'),
        courses=courses,
        allocations=allocations,
        staff=staff,
        active_page="dashboard"
    )

@views.route("/admin/courses")
def admin_courses():
    if not require_admin():
        return redirect(url_for('auth.login'))

    with engine.connect() as conn:
        courses = conn.execute(
            text("SELECT CourseID, CourseName FROM Courses ORDER BY CourseName")
        ).mappings().all()

    return render_template("admin_courses.html", courses=courses, name=session.get('name'), role=session.get('role'))


from sqlalchemy.exc import IntegrityError

# ---------- ADMIN: MANAGE TIMETABLE FOR A COURSE ----------
@views.route("/admin/courses/<course_id>/timetable", methods=["GET", "POST"])
def admin_course_timetable(course_id):
    if not require_admin():
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        # Get raw form values (keep your current form; no dropdown changes)
        day_raw = request.form.get("day", "")
        timeslot_raw = request.form.get("timeslot", "")
        room_raw = request.form.get("room", "")

        # Normalize inputs (lightweight)
        day = day_raw.strip().title()         # e.g. "monday" -> "Monday"
        timeslot = timeslot_raw.strip()       # e.g. "09:00-10:00"
        room = room_raw.strip()               # e.g. "F82"

        # Basic validation
        if not day or not timeslot:
            flash("Day and time slot are required.", "error")
            return render_template("admin_course_timetable.html", course=course, rows=rows, name=session.get('name'), role=session.get('role'))

        try:
            with engine.begin() as conn:
                # 1) Block any two courses sharing same room at same day+time
                clash = conn.execute(
                    text("""
                        SELECT 1
                        FROM timetable_entry
                        WHERE DayOfWeek = :day
                          AND TimeSlot = :slot
                          AND RoomNumber = :room
                        LIMIT 1
                    """),
                    {"day": day, "slot": timeslot, "room": room}
                ).scalar()

                if clash:
                    flash(
                        f"Warning: Room {room} is already booked on {day} at {timeslot}. "
                        f"The slot was still added.",
                        "warning"
                    )

                # 2) Prevent duplicate slot for the same course (same day+time)
                course_dup = conn.execute(
                    text("""
                        SELECT 1
                        FROM timetable_entry
                        WHERE CourseID = :cid
                          AND DayOfWeek = :day
                          AND TimeSlot = :slot
                        LIMIT 1
                    """),
                    {"cid": course_id, "day": day, "slot": timeslot}
                ).scalar()

                if course_dup:
                    flash(f"This course already has a slot on {day} at {timeslot}.", "error")
                    return render_template("admin_course_timetable.html", course=course, rows=rows, name=session.get('name'), role=session.get('role'))

                # 3) Insert
                conn.execute(
                    text("""
                        INSERT INTO timetable_entry (CourseID, DayOfWeek, TimeSlot, RoomNumber)
                        VALUES (:cid, :day, :slot, :room)
                    """),
                    {"cid": course_id, "day": day, "slot": timeslot, "room": room or None}
                )

            flash("Time slot added.", "success")
        except IntegrityError as e:
            # Catches any leftover FK/unique issues
            flash("Could not add the time slot due to a database constraint.", "error")


    # GET: load course & its rows
    with engine.connect() as conn:
        course = conn.execute(
            text("SELECT CourseID, CourseName FROM Courses WHERE CourseID = :cid"),
            {"cid": course_id}
        ).mappings().fetchone()

        rows = conn.execute(
            text("""
                SELECT TimetableID, DayOfWeek, TimeSlot, RoomNumber
                FROM timetable_entry
                WHERE CourseID = :cid
                ORDER BY FIELD(DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday'),
                         TimeSlot
            """),
            {"cid": course_id}
        ).mappings().all()

    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("views.admin_courses"))

    return render_template("admin_course_timetable.html", course=course, rows=rows, name=session.get('name'), role=session.get('role'))


# ---------- ADMIN: DELETE TIMETABLE ROW ----------
@views.route("/admin/timetable/<int:timetable_id>/delete", methods=["POST"])
def admin_delete_timetable_row(timetable_id):
    if not require_admin():
        return redirect(url_for("auth.login"))

    course_id = request.form.get("course_id")

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM timetable_entry WHERE TimetableID = :tid"),
            {"tid": timetable_id}
        )

    flash("Time slot deleted.", "success")
    return redirect(url_for("views.admin_course_timetable", course_id=course_id))



@views.route('/manager/allocations/<course_id>', methods=['GET', 'POST'])
def manager_allocations(course_id):
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    # POST: assign a staff member to this course (no time slot)
    if request.method == 'POST':
        staff_name = (request.form.get('staff_name') or '').strip()

        if not staff_name:
            flash("Please enter a staff name.", "error")
            return redirect(url_for('views.manager_allocations', course_id=course_id))

        with engine.begin() as conn:
            staff = conn.execute(
                text("SELECT StaffID, Name FROM Staff WHERE Name = :n"),
                {"n": staff_name}
            ).mappings().fetchone()

            if not staff:
                flash(f"No staff member named '{staff_name}' was found.", "error")
                return redirect(url_for('views.manager_allocations', course_id=course_id))

            already = conn.execute(
                text("""
                    SELECT 1
                    FROM Allocations
                    WHERE StaffID = :sid AND CourseID = :cid
                    LIMIT 1
                """),
                {"sid": staff["StaffID"], "cid": course_id}
            ).scalar()

            if already:
                flash(f"{staff['Name']} is already allocated to this course.", "warning")
                return redirect(url_for('views.manager_allocations', course_id=course_id))

            conn.execute(
                text("""
                    INSERT INTO Allocations
                        (StaffID, StaffName, CourseID, AllocationDate, AssignedHour, TotalHours, TimeTableID)
                    VALUES
                        (:sid, :sname, :cid, NOW(), NULL, NULL, NULL)
                """),
                {
                    "sid": staff["StaffID"],
                    "sname": staff["Name"],
                    "cid": course_id
                }
            )

        flash(f"{staff['Name']} has been allocated to this course.", "success")
        return redirect(url_for('views.manager_allocations', course_id=course_id))

    # GET: show course, staff allocated, and all timetable slots for this course
    with engine.connect() as conn:
        course = conn.execute(
            text("""
                SELECT CourseID, CourseName, ClassSize, CourseHead, ClassInfo
                FROM Courses
                WHERE CourseID = :cid
            """),
            {"cid": course_id}
        ).mappings().fetchone()

        if not course:
            flash("Course not found.", "error")
            return redirect(url_for('views.manager_dashboard'))

        # staff allocated to this course
        assigned = conn.execute(
        text("""
            SELECT 
                a.StaffID,
                a.StaffName,
                MIN(a.AllocationID) AS AnyAllocationID
            FROM Allocations a
            WHERE a.CourseID = :cid
            GROUP BY a.StaffID, a.StaffName
            ORDER BY a.StaffName
        """),
        {"cid": course_id}
    ).mappings().all()


        # ALL timetable slots for this course (read-only display)
        # ALL timetable slots for this course, plus who (if anyone) is assigned to each slot
        timeslots = conn.execute(
            text("""
                SELECT 
                    te.TimeTableID,
                    te.DayOfWeek,
                    te.TimeSlot,
                    te.RoomNumber,
                    COALESCE(GROUP_CONCAT(a.StaffName SEPARATOR ', '), 'No one') AS AssignedStaff
                FROM timetable_entry te
                LEFT JOIN Allocations a
                    ON a.TimeTableID = te.TimeTableID
                WHERE te.CourseID = :cid
                GROUP BY 
                    te.TimeTableID,
                    te.DayOfWeek,
                    te.TimeSlot,
                    te.RoomNumber
                ORDER BY FIELD(te.DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday'),
                         te.TimeSlot
            """),
            {"cid": course_id}
        ).mappings().all()


    return render_template(
        'manager_allocations.html',
        name=session.get('name'),
        role=session.get('role'),
        course=course,
        assigned=assigned,
        unassigned=timeslots      # reuse the old name so the template still works
    )


@views.route('/manager/allocation/<int:allocation_id>/delete', methods=['POST'])
def manager_delete_allocation(allocation_id):
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    # we need course_id so we can return to the same page
    course_id = request.form.get('course_id')

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM Allocations WHERE AllocationID = :aid"),
            {"aid": allocation_id}
        )

    flash("Staff has been unassigned from that time slot.", "success")
    return redirect(url_for('views.manager_allocations', course_id=course_id))


@views.route('/manager/staff', methods=["GET"])
def manager_staff_list():
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    search = request.args.get("search")
    with engine.connect() as conn:
        if search:
            staff = conn.execute(
                text("""
                    SELECT StaffID, Name, Position FROM Staff
                    WHERE Name LIKE :search
                       OR CAST(StaffID AS CHAR) LIKE :search
                       OR Position LIKE :search
                    ORDER BY Name
                """),
                {"search": f"%{search}%"}           
            ).mappings().all()
        else:
            staff = conn.execute(
                text("SELECT StaffID, Name, Position FROM Staff ORDER BY Name")
            ).mappings().all()

    return render_template(
        'manager_staff_list.html',
        staff=staff,
        name=session.get('name'),
        role=session.get('role')
    )

@views.route('/manager/staff/<int:staff_id>')
def manager_staff_allocations(staff_id):
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    with engine.connect() as conn:
        staff = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE StaffID=:sid"),
            {"sid": staff_id}
        ).mappings().fetchone()

        if not staff:
            flash("Staff not found.", "error")
            return redirect(url_for('views.manager_staff_list'))

        allocations = conn.execute(
            text("""
                SELECT 
                    a.AllocationID,
                    c.CourseName,
                    te.DayOfWeek,
                    te.TimeSlot,
                    te.RoomNumber,
                    a.CourseID
                FROM Allocations a
                JOIN timetable_entry te ON a.TimeTableID = te.TimeTableID
                JOIN Courses c ON te.CourseID = c.CourseID
                WHERE a.StaffID = :sid
                ORDER BY FIELD(te.DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'),
                         te.TimeSlot
            """),
            {"sid": staff_id}
        ).mappings().all()

    return render_template(
        'manager_staff_allocations.html',
        staff=staff,
        allocations=allocations,
        name=session.get('name'),
        role=session.get('role')
    )
@views.route('/manager/unassign/<int:allocation_id>', methods=['POST'])
def manager_unassign_allocation(allocation_id):
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    staff_id = request.form.get("staff_id")  # to return to staff's allocation page

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM Allocations WHERE AllocationID = :aid"),
            {"aid": allocation_id}
        )

    flash("Staff has been unassigned from this slot.", "success")
    return redirect(url_for('views.manager_staff_allocations', staff_id=staff_id))


@views.route('/manager/staff/<int:staff_id>/timetable')
def manager_staff_timetable(staff_id):
    if session.get('role') != 'Manager':
        return redirect(url_for('auth.login'))

    hours = list(range(7, 21))  # 08:00–18:00, change if you want

    with engine.connect() as conn:
        staff = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE StaffID = :sid"),
            {"sid": staff_id}
        ).mappings().fetchone()

        if not staff:
            flash("Staff not found.", "error")
            return redirect(url_for('views.manager_staff_list'))

        allocations = conn.execute(
            text("""
                SELECT 
                    c.CourseName,
                    te.DayOfWeek,
                    te.TimeSlot,
                    te.RoomNumber
                FROM Allocations a
                JOIN timetable_entry te ON a.TimeTableID = te.TimeTableID
                JOIN Courses c ON te.CourseID = c.CourseID
                WHERE a.StaffID = :sid
            """),
            {"sid": staff_id}
        ).mappings().all()

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    # timetable[day][hour] = list of slots
    timetable = {day: {h: [] for h in hours} for day in days}

    for a in allocations:
        day = a["DayOfWeek"]
        if day not in days:
            continue

        try:
            start_str, end_str = [p.strip() for p in a["TimeSlot"].split("-")]
            start_hour = int(start_str[:2])
            end_hour = int(end_str[:2])
        except Exception:
            continue

        for h in range(start_hour, end_hour):
            if h in timetable[day]:
                timetable[day][h].append(a)

    return render_template(
        "manager_staff_timetable.html",
        staff=staff,
        timetable=timetable,
        hours=hours,
        days=days,
        name=session.get('name'),
        role=session.get('role')
    )



@views.route("/admin/hours", methods=["GET", "POST"])
def admin_hours():
    if not require_admin():
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        staff_id = request.form.get("staff_id", type=int)
        hours_raw = (request.form.get("total_hours") or "").strip()
        if not staff_id:
            flash("Invalid staff selection.", "error")
            return redirect(url_for("views.admin_hours"))
        if hours_raw == "":
            new_hours = None
        else:
            try:
                new_hours = int(hours_raw)
                if new_hours < 0:
                    raise ValueError
            except ValueError:
                flash("Total hours must be a non-negative whole number.", "error")
                return redirect(url_for("views.admin_hours"))
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE Staff
                    SET TotalHours = :h
                    WHERE StaffID = :sid
                """),
                {"h": new_hours, "sid": staff_id}
            )
        flash("Working hours updated.", "success")
        return redirect(url_for("views.admin_hours"))

    search = request.args.get("search")
    with engine.connect() as conn:
        if search:
            staff = conn.execute(
                text("""
                    SELECT 
                        s.StaffID,
                        s.Name,
                        s.Position,
                        s.TotalHours AS TargetHours,
                        COALESCE(SUM(a.AssignedHour), 0) AS AllocatedHours
                    FROM Staff s
                    LEFT JOIN Allocations a ON a.StaffID = s.StaffID
                    WHERE s.Name LIKE :search
                       OR CAST(s.StaffID AS CHAR) LIKE :search
                       OR s.Position LIKE :search
                    GROUP BY s.StaffID, s.Name, s.Position, s.TotalHours
                    ORDER BY s.Name
                """),
                {"search": f"%{search}%"}
            ).mappings().all()
        else:
            staff = conn.execute(
                text("""
                    SELECT 
                        s.StaffID,
                        s.Name,
                        s.Position,
                        s.TotalHours AS TargetHours,
                        COALESCE(SUM(a.AssignedHour), 0) AS AllocatedHours
                    FROM Staff s
                    LEFT JOIN Allocations a ON a.StaffID = s.StaffID
                    GROUP BY s.StaffID, s.Name, s.Position, s.TotalHours
                    ORDER BY s.Name
                """)
            ).mappings().all()

    return render_template(
        "admin_hours.html",
        staff=staff,
        name=session.get("name"),
        role=session.get("role")
    )


@views.route('/admin/staff/<int:staff_id>')
def admin_staff_allocations(staff_id):
    if session.get('role') != 'Admin':
        return redirect(url_for('auth.login'))

    with engine.connect() as conn:
        staff = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE StaffID=:sid"),
            {"sid": staff_id}
        ).mappings().fetchone()

        if not staff:
            flash("Staff not found.", "error")
            return redirect(url_for('views.admin_staff_list'))

        allocations = conn.execute(
            text("""
                SELECT 
                    a.AllocationID,
                    c.CourseName,
                    te.DayOfWeek,
                    te.TimeSlot,
                    te.RoomNumber,
                    a.CourseID
                FROM Allocations a
                JOIN timetable_entry te ON a.TimeTableID = te.TimeTableID
                JOIN Courses c ON te.CourseID = c.CourseID
                WHERE a.StaffID = :sid
                ORDER BY FIELD(te.DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'),
                         te.TimeSlot
            """),
            {"sid": staff_id}
        ).mappings().all()

    return render_template(
        'admin_staff_allocations.html',
        staff=staff,
        allocations=allocations,
        name=session.get('name'),
        role=session.get('role'),
        active_page='admin_staff'
    )

@views.route('/admin/allocation/<int:allocation_id>/delete', methods=['POST'])
def admin_delete_allocation(allocation_id):
    if session.get('role') != 'Admin':
        return redirect(url_for('auth.login'))

    staff_id = request.form.get("staff_id")

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Allocations WHERE AllocationID = :aid"), {"aid": allocation_id})

    flash("Staff has been unassigned from this slot.", "success")
    return redirect(url_for('views.admin_staff_allocations', staff_id=staff_id))


@views.route('/admin/staff/<int:staff_id>/timetable')
def admin_staff_timetable(staff_id):
    if session.get('role') != 'Admin':
        return redirect(url_for('auth.login'))

    with engine.connect() as conn:
        staff = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE StaffID = :sid"),
            {"sid": staff_id}
        ).mappings().fetchone()

        allocations = conn.execute(
            text("""
                SELECT a.AllocationID,
                       c.CourseName,
                       te.DayOfWeek,
                       te.TimeSlot,
                       te.RoomNumber
                FROM Allocations a
                JOIN timetable_entry te ON a.TimeTableID = te.TimeTableID
                JOIN Courses c ON te.CourseID = c.CourseID
                WHERE a.StaffID = :sid
                ORDER BY FIELD(te.DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday'),
                         te.TimeSlot
            """),
            {"sid": staff_id}
        ).mappings().all()

    # make them mutable dicts & add StartHour / EndHour
    allocs = []
    for a in allocations:
        d = dict(a)
        try:
            start_str, end_str = [p.strip() for p in d["TimeSlot"].split("-")]
            d["StartHour"] = int(start_str[:2])  # "09:00" -> 9
            d["EndHour"] = int(end_str[:2])      # "15:00" -> 15
        except Exception:
            d["StartHour"] = None
            d["EndHour"] = None
        allocs.append(d)

    return render_template(
        "admin_staff_timetable.html",
        staff=staff,
        allocations=allocs,
        name=session.get('name'),
        role=session.get('role'),
        active_page='admin_staff'
    )


def timeslot_to_hours(timeslot: str) -> int:
    """
    Convert '09:00-10:00' into number of hours (int).
    Assumes times within same day and whole hours.
    """
    try:
        start_str, end_str = [p.strip() for p in timeslot.split('-')]
        fmt = "%H:%M"
        start = datetime.datetime.strptime(start_str, fmt)
        end = datetime.datetime.strptime(end_str, fmt)
        delta = end - start
        hours = delta.seconds // 3600   # integer hours
        if hours <= 0:
            return 1  # fallback: treat as 1 hour
        return hours
    except Exception:
        return 1  # safe fallback

@views.route('/notifications', methods=['GET', 'POST'])
def notifications():
    # Require logged-in user
    if not session.get('username'):
        return redirect(url_for('auth.login'))

    username = session.get('username')

    # Try to get staff_id from session
    staff_id = session.get('staff_id')

    # If missing, try to recover it from the username
    if not staff_id:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT StaffID FROM Staff WHERE Username = :u"),
                {"u": username}
            ).mappings().fetchone()

        if not row:
            flash("Staff ID missing from session. Please log in again.", "error")
            return redirect(url_for('auth.login'))

        staff_id = row['StaffID']
        session['staff_id'] = staff_id  # cache it

    # If sending a new message
    if request.method == 'POST':
        receiver_id = request.form.get('receiver_id', type=int)
        message = (request.form.get('message') or '').strip()

        if not receiver_id or not message:
            flash("Please select a recipient and enter a message.", "error")
        else:
            with engine.begin() as conn:
                # Ensure receiver exists
                receiver = conn.execute(
                    text("SELECT StaffID FROM Staff WHERE StaffID = :sid"),
                    {"sid": receiver_id}
                ).mappings().fetchone()

                if not receiver:
                    flash("Selected recipient does not exist.", "error")
                else:
                    conn.execute(
                        text("""
                            INSERT INTO Notifications (SenderID, ReceiverID, MessageText)
                            VALUES (:sender, :receiver, :msg)
                        """),
                        {"sender": staff_id, "receiver": receiver_id, "msg": message}
                    )
                    flash("Message sent.", "success")

    # GET (or after POST) – show inbox + list of staff for sending
    with engine.connect() as conn:
        # ✅ Inbox: messages where the *receiver's username* matches the logged-in user
        inbox = conn.execute(
            text("""
                SELECT n.NotificationID,
                       n.MessageText,
                       n.IsRead,
                       n.CreatedAt,
                       s.Name AS SenderName
                FROM Notifications n
                JOIN Staff s ON n.SenderID = s.StaffID
                JOIN Staff r ON n.ReceiverID = r.StaffID
                WHERE r.Username = :uname
                ORDER BY n.CreatedAt DESC
            """),
            {"uname": username}
        ).mappings().all()

        # For dropdown to send messages to others
        staff_list = conn.execute(
            text("""
                SELECT StaffID, Name
                FROM Staff
                WHERE StaffID != :me
                ORDER BY Name
            """),
            {"me": staff_id}
        ).mappings().all()

    role = session.get('role')
    if role == "Admin":
        template_name = "admin_notifications.html"
    elif role == "Manager":
        template_name = "manager_notifications.html"
    else:  # Staff
        template_name = "staff_notifications.html"

    return render_template(
        template_name,
        name=session.get('name'),
        role=session.get('role'),
        inbox=inbox,
        staff_list=staff_list,
        active_pages="notifications"
    )

@views.route('/faq')
def faq():
    return render_template(
        'faq.html',
        name=session.get('name'),
        role=session.get('role')
    )




@views.route('/notifications/<int:notif_id>/toggle', methods=['POST'])
def toggle_notification(notif_id):
    if not session.get('username'):
        return redirect(url_for('auth.login'))

    username = session.get('username')

    with engine.begin() as conn:
        # Only allow the receiver (by username) to mark their own notification as read
        conn.execute(
            text("""
                UPDATE Notifications n
                JOIN Staff r ON n.ReceiverID = r.StaffID
                SET n.IsRead = 1
                WHERE n.NotificationID = :nid
                  AND r.Username = :uname
            """),
            {"nid": notif_id, "uname": username}
        )

    return redirect(url_for('views.notifications'))

@views.route('/my_timetable', methods=['GET', 'POST'])
def my_timetable():
    # Ensure user is logged in
    if not session.get('username'):
        return redirect(url_for('auth.login'))

    username = session.get('username')

    # ---------- POST: assign or de-assign ----------
    if request.method == 'POST':
        timetable_id = request.form.get('timetable_id', type=int)
        course_id = request.form.get('course_id')
        mode = request.form.get('mode', 'assign')  # "assign" or "unassign"

        if not timetable_id or not course_id:
            flash("Invalid time slot selection.", "error")
            return redirect(url_for('views.my_timetable'))

        with engine.begin() as conn:
            # Get current staff
            staff = conn.execute(
                text("SELECT StaffID, Name FROM Staff WHERE Username = :u"),
                {"u": username}
            ).mappings().fetchone()

            if not staff:
                flash("Staff not found.", "error")
                return redirect(url_for('auth.login'))

            # --- UNASSIGN MODE ---
            if mode == 'unassign':
                conn.execute(
                    text("""
                        UPDATE Allocations
                        SET TimeTableID = NULL
                        WHERE StaffID = :sid
                          AND TimeTableID = :tid
                          AND CourseID  = :cid
                    """),
                    {
                        "sid": staff["StaffID"],
                        "tid": timetable_id,
                        "cid": course_id
                    }
                )
                flash("You have been removed from that time slot.", "success")
                return redirect(url_for('views.my_timetable'))

            # --- ASSIGN MODE (default) ---
            # Prevent double-booking the same slot for this staff
            already = conn.execute(
                text("""
                    SELECT 1
                    FROM Allocations
                    WHERE StaffID = :sid
                      AND TimeTableID = :tid
                    LIMIT 1
                """),
                {"sid": staff["StaffID"], "tid": timetable_id}
            ).scalar()

            if already:
                flash("You are already booked into that time slot.", "warning")
                return redirect(url_for('views.my_timetable'))

            # Insert a new allocation row for this chosen slot
            conn.execute(
                text("""
                    INSERT INTO Allocations (StaffID, StaffName, CourseID, TimeTableID, AllocationDate)
                    VALUES (:sid, :sname, :cid, :tid, CURDATE())
                """),
                {
                    "sid": staff["StaffID"],
                    "sname": staff["Name"],
                    "cid": course_id,
                    "tid": timetable_id,
                }
            )

        flash("Time slot chosen successfully.", "success")
        return redirect(url_for('views.my_timetable'))

    # ---------- GET: show timetable & courses/slots ----------
    with engine.connect() as conn:
        # Staff info
        staff = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE Username = :u"),
            {"u": username}
        ).mappings().fetchone()

        if not staff:
            flash("Staff not found.", "error")
            return redirect(url_for('auth.login'))

        # 1) Confirmed allocations (anything with a timetable slot)
        confirmed_rows = conn.execute(
            text("""
                SELECT a.AllocationID,
                       c.CourseName,
                       te.DayOfWeek,
                       te.TimeSlot,
                       te.RoomNumber
                FROM Allocations a
                JOIN timetable_entry te ON a.TimeTableID = te.TimeTableID
                JOIN Courses c ON te.CourseID = c.CourseID
                WHERE a.StaffID = :sid
                ORDER BY FIELD(te.DayOfWeek,'Monday','Tuesday','Wednesday','Thursday','Friday'),
                         te.TimeSlot
            """),
            {"sid": staff['StaffID']}
        ).mappings().all()

        # Build allocations with StartHour / EndHour for the timetable grid (robust parsing)
        allocations = []
        for row in confirmed_rows:
            d = dict(row)
            try:
                # Works with "09:00-10:00", "09:00 - 10:00", etc.
                start_str, end_str = [p.strip() for p in d["TimeSlot"].split("-")]
                start_dt = datetime.datetime.strptime(start_str, "%H:%M")
                end_dt = datetime.datetime.strptime(end_str, "%H:%M")
                d["StartHour"] = start_dt.hour
                d["EndHour"] = end_dt.hour
            except Exception:
                d["StartHour"] = None
                d["EndHour"] = None
            allocations.append(d)

        # 2) Courses this staff can choose from:
        #    any course that has timetabled slots
     # 2) Courses this staff is actually assigned to
    #    (only courses where they already have an Allocation row)
        pending_courses = conn.execute(
            text("""
                SELECT DISTINCT
                    c.CourseID,
                    c.CourseName
                FROM Allocations a
                JOIN Courses c ON a.CourseID = c.CourseID
                WHERE a.StaffID = :sid
                ORDER BY c.CourseName
            """),
            {"sid": staff["StaffID"]}
        ).mappings().all()


        # 3) For each course, show ALL slots + whether this staff has chosen them
        #    and which staff (if any) are currently allocated to that slot.
        course_slots = {}
        for pc in pending_courses:
            slots = conn.execute(
                text("""
                    SELECT 
                        te.TimeTableID,
                        te.DayOfWeek,
                        te.TimeSlot,
                        te.RoomNumber,

                        -- Is THIS logged-in staff member on this slot?
                        EXISTS (
                            SELECT 1
                            FROM Allocations a
                            WHERE a.TimeTableID = te.TimeTableID
                              AND a.StaffID = :sid
                        ) AS IsChosen,

                        -- All staff currently allocated to this slot (could be multiple)
                        GROUP_CONCAT(a2.StaffName ORDER BY a2.StaffName SEPARATOR ', ') AS AllocatedStaff

                    FROM timetable_entry te
                    LEFT JOIN Allocations a2
                           ON a2.TimeTableID = te.TimeTableID

                    WHERE te.CourseID = :cid

                    GROUP BY
                        te.TimeTableID,
                        te.DayOfWeek,
                        te.TimeSlot,
                        te.RoomNumber

                    ORDER BY
                        FIELD(te.DayOfWeek,
                              'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'),
                        te.TimeSlot
                """),
                {"cid": pc["CourseID"], "sid": staff["StaffID"]}
            ).mappings().all()

            course_slots[pc["CourseID"]] = slots

    # Pick template per role
    role = session.get('role')
    if role == "Admin":
        template_name = "admin_my_timetable.html"
    elif role == "Manager":
        template_name = "manager_my_timetable.html"
    else:  # Staff
        template_name = "staff_my_timetable.html"

    return render_template(
        template_name,
        staff=staff,
        allocations=allocations,
        pending_courses=pending_courses,
        course_slots=course_slots,
        name=session.get('name'),
        role=session.get('role')
    )





@views.route('/reports', methods=['GET', 'POST'])
def reports():
    # Require login
    if not session.get('username'):
        return redirect(url_for('auth.login'))

    # Only Staff & Manager can use reports
    if session.get('role') not in ['Staff', 'Manager']:
        flash("Only Staff and Managers can submit reports.", "error")
        return redirect(url_for('views.notifications'))

    username = session.get('username')

    # Get the current staff member
    with engine.connect() as conn:
        me = conn.execute(
            text("SELECT StaffID, Name, Position FROM Staff WHERE Username = :u"),
            {"u": username}
        ).mappings().fetchone()

    if not me:
        flash("User not found.", "error")
        return redirect(url_for('auth.login'))

    # ----- HANDLE POST (submit report) -----
    if request.method == 'POST':
        report_type = (request.form.get('report_type') or '').strip()
        receiver_id = request.form.get('receiver_id', type=int)
        message = (request.form.get('message') or '').strip()

        if not report_type or not receiver_id or not message:
            flash("All fields are required.", "error")
            return redirect(url_for('views.reports'))

        with engine.begin() as conn:
            receiver = conn.execute(
                text("SELECT StaffID, Position FROM Staff WHERE StaffID = :rid"),
                {"rid": receiver_id}
            ).mappings().fetchone()

            if not receiver:
                flash("Selected recipient does not exist.", "error")
                return redirect(url_for('views.reports'))

            # Server-side enforcement of allowed roles per request type
            allowed_roles = []

            if report_type == "Course Change Request":
                allowed_roles = ["Admin", "Manager"]
            elif report_type == "Hours Change Request":
                allowed_roles = ["Admin"]
            elif report_type == "Message Staff":
                allowed_roles = ["Admin", "Manager", "Staff"]
            else:
                flash("Invalid report type.", "error")
                return redirect(url_for('views.reports'))

            if receiver["Position"] not in allowed_roles:
                flash("You cannot send this type of request to that role.", "error")
                return redirect(url_for('views.reports'))

            # Insert into Notifications
            conn.execute(
                text("""
                    INSERT INTO Notifications (SenderID, ReceiverID, MessageText)
                    VALUES (:sid, :rid, :msg)
                """),
                {
                    "sid": me["StaffID"],
                    "rid": receiver["StaffID"],
                    "msg": f"{report_type}:\n{message}"
                }
            )

        flash("Request sent successfully.", "success")
        return redirect(url_for('views.reports'))

    # ----- HANDLE GET (load dropdowns) -----
    with engine.connect() as conn:
        # Admins only (for Hours Change)
        admins = conn.execute(
            text("""
                SELECT StaffID, Name, Position
                FROM Staff
                WHERE Position = 'Admin'
                ORDER BY Name
            """)
        ).mappings().all()

        # Admins + Managers (for Course Change)
        admin_managers = conn.execute(
            text("""
                SELECT StaffID, Name, Position
                FROM Staff
                WHERE Position IN ('Admin', 'Manager')
                ORDER BY Position, Name
            """)
        ).mappings().all()

        # All staff except self (for Message Staff)
        all_staff = conn.execute(
            text("""
                SELECT StaffID, Name, Position
                FROM Staff
                WHERE StaffID != :me
                ORDER BY Name
            """),
            {"me": me["StaffID"]}
        ).mappings().all()


    role = session.get('role')
    if role == "Admin":
        template_name = "admin_reports.html"
    elif role == "Manager":
        template_name = "manager_reports.html"
    else:  # Staff
        template_name = "staff_reports.html"

    return render_template(
        template_name,
        name=session.get('name'),
        role=session.get('role'),
        me=me,
        admins=admins,
        admin_managers=admin_managers,
        all_staff=all_staff
    )
