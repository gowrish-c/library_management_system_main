import os
from datetime import date, timedelta
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

# Vercel Functions do not provide a durable application filesystem.
# Use Supabase PostgreSQL in production. For local development, SQLite remains
# available when DATABASE_URL is not set.
database_url = os.environ.get("DATABASE_URL", "").strip().strip("'").strip('"')
if database_url:
    # Vercel/Neon provides a PostgreSQL DATABASE_URL. Normalize old postgres:// URLs.
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]

    # Neon pooled PostgreSQL is suitable for Vercel serverless functions.
    # NullPool avoids one SQLAlchemy pool per short-lived Vercel instance.
    # Prepared statements are disabled because PgBouncer-style transaction
    # pooling can otherwise reuse a statement name on a different connection.
    connect_args = {
        "sslmode": "require",
        "prepare_threshold": None,
        "connect_timeout": 10,
    }
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "poolclass": NullPool,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
else:
    if os.environ.get("VERCEL"):
        raise RuntimeError(
            "DATABASE_URL is required on Vercel. Connect a Neon Postgres database "
            "to the Vercel project or add DATABASE_URL in Vercel Environment Variables."
        )
    # Local development only. Vercel never uses this SQLite file.
    database_url = "sqlite:///" + os.path.join(basedir, "library.db")
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"

DEFAULT_LOAN_DAYS = 14


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.Date, default=date.today)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(150), nullable=False)
    isbn = db.Column(db.String(30), unique=True)
    category = db.Column(db.String(80))
    quantity = db.Column(db.Integer, default=1, nullable=False)
    added_on = db.Column(db.Date, default=date.today)

    @property
    def available_copies(self):
        issued = IssueRecord.query.filter_by(book_id=self.id, status="issued").count()
        return max(0, self.quantity - issued)


class IssueRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    issued_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    issue_date = db.Column(db.Date, default=date.today)
    due_date = db.Column(db.Date)
    return_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="issued")

    book = db.relationship("Book", backref="issue_records")
    student = db.relationship("User", foreign_keys=[student_id])
    librarian = db.relationship("User", foreign_keys=[issued_by])

    @property
    def is_overdue(self):
        return self.status == "issued" and self.due_date and date.today() > self.due_date


class BookRequest(db.Model):
    """Persistent student requests. Added without removing existing tables/data."""
    __tablename__ = "book_request"

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    requested_at = db.Column(db.Date, default=date.today, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    processed_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    processed_at = db.Column(db.Date)
    note = db.Column(db.String(255))

    book = db.relationship("Book", backref="book_requests")
    student = db.relationship("User", foreign_keys=[student_id])
    processor = db.relationship("User", foreign_keys=[processed_by])


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                if wants_json():
                    return jsonify(ok=False, message="You do not have permission."), 403
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


def wants_json():
    return request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
           "application/json" in request.headers.get("Accept", "")


def json_ok(message, **extra):
    return jsonify(ok=True, message=message, **extra)


def json_error(message, code=400, **extra):
    return jsonify(ok=False, message=message, **extra), code


def book_payload(book):
    pending = 0
    if current_user.is_authenticated and current_user.role == "student":
        pending = BookRequest.query.filter_by(
            book_id=book.id, student_id=current_user.id, status="pending"
        ).count()
    return {
        "id": book.id, "title": book.title, "author": book.author,
        "isbn": book.isbn or "", "category": book.category or "",
        "quantity": book.quantity, "available": book.available_copies,
        "pending_request": bool(pending)
    }


# ---------------- Authentication ----------------
@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))


@app.route("/favicon.ico")
@app.route("/favicon.png")
def favicon():
    # Avoid noisy 404/500 requests when browsers ask for a favicon.
    return "", 204


@app.get("/health")
def health():
    """Small deployment check that verifies the database is reachable."""
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify(ok=True, app="Library Management System", database="connected")
    except Exception:
        db.session.rollback()
        return jsonify(ok=False, app="Library Management System", database="unavailable"), 503


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash(f"Welcome back, {user.full_name}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for({
        "admin": "admin_dashboard",
        "librarian": "librarian_dashboard",
        "student": "student_dashboard"
    }[current_user.role]))


# ---------------- Dashboard ----------------
@app.route("/admin/dashboard")
@roles_required("admin")
def admin_dashboard():
    return render_template("admin_dashboard.html")


@app.route("/librarian/dashboard")
@roles_required("librarian", "admin")
def librarian_dashboard():
    return render_template("librarian_dashboard.html")


@app.route("/student/dashboard")
@roles_required("student")
def student_dashboard():
    return render_template("student_dashboard.html")


@app.get("/api/dashboard")
@login_required
def api_dashboard():
    active = IssueRecord.query.filter_by(status="issued").order_by(IssueRecord.due_date).all()
    if current_user.role == "admin":
        stats = {
            "total_books": Book.query.count(),
            "total_copies": db.session.query(db.func.sum(Book.quantity)).scalar() or 0,
            "total_students": User.query.filter_by(role="student").count(),
            "total_librarians": User.query.filter_by(role="librarian").count(),
            "books_issued": len(active),
            "overdue": sum(1 for r in active if r.is_overdue),
        }
        return jsonify(
            role="admin", stats=stats,
            recent=[issue_json(r) for r in IssueRecord.query.order_by(
                IssueRecord.issue_date.desc(), IssueRecord.id.desc()).limit(8).all()]
        )

    if current_user.role == "librarian":
        stats = {
            "total_books": Book.query.count(),
            "books_issued": len(active),
            "overdue": sum(1 for r in active if r.is_overdue),
            "total_students": User.query.filter_by(role="student").count(),
        }
        return jsonify(
            role="librarian", stats=stats,
            active=[issue_json(r) for r in active],
            requests=[request_json(r) for r in BookRequest.query.filter_by(
                status="pending").order_by(BookRequest.id.desc()).all()]
        )

    my_active = IssueRecord.query.filter_by(
        student_id=current_user.id, status="issued"
    ).order_by(IssueRecord.due_date).all()
    history_count = IssueRecord.query.filter_by(student_id=current_user.id).count()
    my_requests = BookRequest.query.filter_by(
        student_id=current_user.id).order_by(BookRequest.id.desc()).limit(20).all()
    return jsonify(
        role="student",
        stats={"borrowed": len(my_active), "history": history_count, "titles": Book.query.count()},
        active=[issue_json(r) for r in my_active],
        requests=[request_json(r) for r in my_requests]
    )


def issue_json(r):
    return {
        "id": r.id, "book": r.book.title, "author": r.book.author,
        "student": r.student.full_name, "issued": r.issue_date.isoformat() if r.issue_date else "",
        "due": r.due_date.isoformat() if r.due_date else "",
        "returned": r.return_date.isoformat() if r.return_date else "",
        "status": r.status, "overdue": bool(r.is_overdue)
    }


def request_json(r):
    return {
        "id": r.id, "book_id": r.book_id, "book": r.book.title,
        "student": r.student.full_name, "status": r.status,
        "requested_at": r.requested_at.isoformat() if r.requested_at else "",
        "note": r.note or "", "available": r.book.available_copies
    }


# ---------------- Books ----------------
@app.route("/books")
@login_required
def books():
    query = request.args.get("q", "").strip()
    if query:
        like = f"%{query}%"
        all_books = Book.query.filter(
            db.or_(Book.title.ilike(like), Book.author.ilike(like), Book.isbn.ilike(like))
        ).order_by(Book.title).all()
    else:
        all_books = Book.query.order_by(Book.title).all()
    return render_template("books.html", books=all_books, query=query)


@app.get("/api/books")
@login_required
def api_books():
    query = request.args.get("q", "").strip()
    q = Book.query
    if query:
        like = f"%{query}%"
        q = q.filter(db.or_(Book.title.ilike(like), Book.author.ilike(like), Book.isbn.ilike(like)))
    return jsonify(books=[book_payload(b) for b in q.order_by(Book.title).all()])


@app.route("/books/add", methods=["GET", "POST"])
@roles_required("admin", "librarian")
def add_book():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        isbn = request.form.get("isbn", "").strip() or None
        category = request.form.get("category", "").strip()
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            quantity = 1
        if not title or not author or quantity < 1:
            return json_error("Valid title, author and quantity are required.") if wants_json() else (
                flash("Valid title, author and quantity are required.", "danger") or render_template("add_book.html")
            )
        if isbn and Book.query.filter_by(isbn=isbn).first():
            return json_error("A book with that ISBN already exists.") if wants_json() else (
                flash("A book with that ISBN already exists.", "danger") or render_template("add_book.html")
            )
        book = Book(title=title, author=author, isbn=isbn, category=category, quantity=quantity)
        db.session.add(book)
        db.session.commit()
        return json_ok(f'"{title}" was added to the catalog.') if wants_json() else (
            flash(f'"{title}" was added to the catalog.', "success") or redirect(url_for("books"))
        )
    return render_template("add_book.html")


@app.route("/books/edit/<int:book_id>", methods=["GET", "POST"])
@roles_required("admin", "librarian")
def edit_book(book_id):
    book = db.get_or_404(Book, book_id)
    if request.method == "POST":
        book.title = request.form.get("title", book.title).strip()
        book.author = request.form.get("author", book.author).strip()
        isbn = request.form.get("isbn", "").strip() or None
        duplicate = Book.query.filter(Book.isbn == isbn, Book.id != book.id).first() if isbn else None
        if duplicate:
            return json_error("Another book already uses that ISBN.") if wants_json() else (
                flash("Another book already uses that ISBN.", "danger") or render_template("edit_book.html", book=book)
            )
        book.isbn = isbn
        book.category = request.form.get("category", "").strip()
        try:
            quantity = int(request.form.get("quantity", str(book.quantity)))
        except ValueError:
            quantity = book.quantity
        issued = IssueRecord.query.filter_by(book_id=book.id, status="issued").count()
        if quantity < max(1, issued):
            return json_error(f"Quantity cannot be less than {issued} currently issued copies.") if wants_json() else (
                flash(f"Quantity cannot be less than {issued} currently issued copies.", "danger") or
                render_template("edit_book.html", book=book)
            )
        book.quantity = quantity
        db.session.commit()
        return json_ok(f'"{book.title}" was updated.') if wants_json() else (
            flash(f'"{book.title}" was updated.', "success") or redirect(url_for("books"))
        )
    return render_template("edit_book.html", book=book)


@app.post("/books/delete/<int:book_id>")
@roles_required("admin", "librarian")
def delete_book(book_id):
    book = db.get_or_404(Book, book_id)
    if IssueRecord.query.filter_by(book_id=book.id, status="issued").first():
        return json_error("Cannot delete a book that currently has copies issued.") if wants_json() else (
            flash("Cannot delete a book that currently has copies issued.", "danger") or redirect(url_for("books"))
        )
    # Remove request rows for this book before deleting it.
    BookRequest.query.filter_by(book_id=book.id).delete(synchronize_session=False)
    db.session.delete(book)
    db.session.commit()
    return json_ok("Book removed from catalog.") if wants_json() else (
        flash("Book removed from catalog.", "info") or redirect(url_for("books"))
    )


# ---------------- Issue / return ----------------
@app.route("/issue", methods=["GET", "POST"])
@roles_required("admin", "librarian")
def issue_book():
    students = User.query.filter_by(role="student").order_by(User.full_name).all()
    available_books = [b for b in Book.query.order_by(Book.title).all() if b.available_copies > 0]
    if request.method == "POST":
        try:
            book_id, student_id = int(request.form.get("book_id")), int(request.form.get("student_id"))
        except (TypeError, ValueError):
            return json_error("Please select a valid book and student.") if wants_json() else (
                flash("Please select a valid book and student.", "danger") or
                render_template("issue_book.html", students=students, books=available_books, default_days=DEFAULT_LOAN_DAYS)
            )
        try:
            days = max(1, int(request.form.get("days", DEFAULT_LOAN_DAYS)))
        except ValueError:
            days = DEFAULT_LOAN_DAYS
        book, student = db.session.get(Book, book_id), db.session.get(User, student_id)
        if not book or not student or student.role != "student":
            msg = "Please select a valid book and student."
        elif book.available_copies <= 0:
            msg = "No copies of that book are currently available."
        elif IssueRecord.query.filter_by(book_id=book.id, student_id=student.id, status="issued").first():
            msg = "This student already has this book."
        else:
            record = IssueRecord(
                book_id=book.id, student_id=student.id, issued_by=current_user.id,
                issue_date=date.today(), due_date=date.today() + timedelta(days=days), status="issued"
            )
            db.session.add(record)
            db.session.commit()
            # Resolve a pending request automatically if the issue came from one.
            pending = BookRequest.query.filter_by(
                book_id=book.id, student_id=student.id, status="pending"
            ).order_by(BookRequest.id.asc()).first()
            if pending:
                pending.status = "approved"
                pending.processed_by = current_user.id
                pending.processed_at = date.today()
                pending.note = "Book issued."
                db.session.commit()
            msg = f'"{book.title}" issued to {student.full_name}.'
            if wants_json():
                return json_ok(msg, book=book_payload(book))
            flash(msg, "success")
            return redirect(url_for("issue_book"))
        return json_error(msg) if wants_json() else (
            flash(msg, "danger") or render_template(
                "issue_book.html", students=students,
                books=[b for b in Book.query.order_by(Book.title).all() if b.available_copies > 0],
                default_days=DEFAULT_LOAN_DAYS)
        )
    return render_template("issue_book.html", students=students, books=available_books, default_days=DEFAULT_LOAN_DAYS)


@app.post("/return/<int:record_id>")
@roles_required("admin", "librarian")
def return_book(record_id):
    record = db.get_or_404(IssueRecord, record_id)
    if record.status == "issued":
        record.status, record.return_date = "returned", date.today()
        db.session.commit()
        msg = f'"{record.book.title}" marked as returned.'
    else:
        msg = "This loan has already been returned."
    return json_ok(msg) if wants_json() else (flash(msg, "success") or redirect(request.referrer or url_for("librarian_dashboard")))


# ---------------- Student book requests ----------------
@app.post("/request-book/<int:book_id>")
@roles_required("student")
def request_book(book_id):
    book = db.get_or_404(Book, book_id)
    if IssueRecord.query.filter_by(book_id=book.id, student_id=current_user.id, status="issued").first():
        return json_error("You already have this book.")
    if BookRequest.query.filter_by(book_id=book.id, student_id=current_user.id, status="pending").first():
        return json_error("You already have a pending request for this book.")
    req = BookRequest(book_id=book.id, student_id=current_user.id, status="pending")
    db.session.add(req)
    db.session.commit()
    return json_ok(f'Request for "{book.title}" sent to the librarian.', request_id=req.id)


@app.post("/requests/<int:request_id>/cancel")
@roles_required("student")
def cancel_request(request_id):
    req = db.get_or_404(BookRequest, request_id)
    if req.student_id != current_user.id:
        return json_error("You cannot cancel this request.", 403)
    if req.status != "pending":
        return json_error("Only pending requests can be cancelled.")
    req.status = "cancelled"
    req.note = "Cancelled by student."
    db.session.commit()
    return json_ok("Request cancelled.")


@app.post("/requests/<int:request_id>/approve")
@roles_required("admin", "librarian")
def approve_request(request_id):
    req = db.get_or_404(BookRequest, request_id)
    if req.status != "pending":
        return json_error("This request is no longer pending.")
    book, student = req.book, req.student
    if book.available_copies <= 0:
        return json_error("No copy is currently available. The request remains pending.")
    if IssueRecord.query.filter_by(book_id=book.id, student_id=student.id, status="issued").first():
        req.status, req.processed_by, req.processed_at = "rejected", current_user.id, date.today()
        req.note = "Student already has this book."
        db.session.commit()
        return json_error("Student already has this book.")
    record = IssueRecord(
        book_id=book.id, student_id=student.id, issued_by=current_user.id,
        issue_date=date.today(), due_date=date.today() + timedelta(days=DEFAULT_LOAN_DAYS), status="issued"
    )
    db.session.add(record)
    req.status, req.processed_by, req.processed_at = "approved", current_user.id, date.today()
    req.note = "Approved and issued."
    db.session.commit()
    return json_ok(f'"{book.title}" issued to {student.full_name}.')


@app.post("/requests/<int:request_id>/reject")
@roles_required("admin", "librarian")
def reject_request(request_id):
    req = db.get_or_404(BookRequest, request_id)
    if req.status != "pending":
        return json_error("This request is no longer pending.")
    req.status, req.processed_by, req.processed_at = "rejected", current_user.id, date.today()
    req.note = "Rejected by librarian."
    db.session.commit()
    return json_ok("Book request rejected.")


# ---------------- People management ----------------
@app.route("/students", methods=["GET", "POST"])
@roles_required("admin")
def students():
    if request.method == "POST":
        msg = create_user("student")
        if wants_json():
            return json_ok(msg)
        flash(msg, "success")
        return redirect(url_for("students"))
    return render_template("students.html", students=User.query.filter_by(role="student").order_by(User.full_name).all())


@app.post("/students/delete/<int:user_id>")
@roles_required("admin")
def delete_student(user_id):
    return delete_user(user_id, "student")


@app.route("/librarians", methods=["GET", "POST"])
@roles_required("admin")
def librarians():
    if request.method == "POST":
        msg = create_user("librarian")
        if wants_json():
            return json_ok(msg)
        flash(msg, "success")
        return redirect(url_for("librarians"))
    return render_template("librarians.html", librarians=User.query.filter_by(role="librarian").order_by(User.full_name).all())


@app.post("/librarians/delete/<int:user_id>")
@roles_required("admin")
def delete_librarian(user_id):
    return delete_user(user_id, "librarian")


def create_user(role):
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    if not username or not full_name or not password:
        raise ValueError("Username, full name, and password are required.")
    if User.query.filter_by(username=username).first():
        raise ValueError("That username is already taken.")
    user = User(username=username, full_name=full_name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return f"{role.capitalize()} account created for {full_name}."


def delete_user(user_id, role):
    user = User.query.filter_by(id=user_id, role=role).first_or_404()
    if role == "student":
        if IssueRecord.query.filter_by(student_id=user.id, status="issued").first():
            return json_error("Cannot remove a student who currently has a book.") if wants_json() else (
                flash("Cannot remove a student who currently has a book.", "danger") or redirect(url_for("students"))
            )
        BookRequest.query.filter_by(student_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    msg = f"{role.capitalize()} account removed."
    return json_ok(msg) if wants_json() else (flash(msg, "info") or redirect(url_for("students" if role == "student" else "librarians")))


# ---------------- My books ----------------
@app.route("/my-books")
@roles_required("student")
def my_books():
    return render_template("my_books.html")


# ---------------- API helpers for live UI ----------------
@app.get("/api/students")
@roles_required("admin", "librarian")
def api_students():
    return jsonify(students=[{"id": s.id, "name": s.full_name, "username": s.username}
                             for s in User.query.filter_by(role="student").order_by(User.full_name).all()])


# ---------------- DB init / migration ----------------
def init_db():
    with app.app_context():
        # create_all preserves all existing tables and data and creates only missing tables.
        db.create_all()
        seed = False
        if not User.query.filter_by(role="admin").first():
            admin = User(username="admin", full_name="System Administrator",
                         email="admin@library.local", role="admin")
            admin.set_password("admin123")
            db.session.add(admin); seed = True
        if not User.query.filter_by(username="librarian1").first():
            librarian = User(username="librarian1", full_name="Jane Librarian",
                             email="librarian@library.local", role="librarian")
            librarian.set_password("librarian123")
            db.session.add(librarian); seed = True
        if not User.query.filter_by(username="student1").first():
            student = User(username="student1", full_name="Alex Student",
                           email="student@library.local", role="student")
            student.set_password("student123")
            db.session.add(student); seed = True
        if Book.query.count() == 0:
            db.session.bulk_save_objects([
                Book(title="The Pragmatic Programmer", author="David Thomas", isbn="9780135957059", category="Technology", quantity=3),
                Book(title="Clean Code", author="Robert C. Martin", isbn="9780132350884", category="Technology", quantity=2),
                Book(title="To Kill a Mockingbird", author="Harper Lee", isbn="9780061120084", category="Fiction", quantity=4),
                Book(title="A Brief History of Time", author="Stephen Hawking", isbn="9780553380163", category="Science", quantity=2),
                Book(title="Sapiens", author="Yuval Noah Harari", isbn="9780062316097", category="History", quantity=3),
            ])
            seed = True
        if seed:
            try:
                db.session.commit()
            except IntegrityError:
                # Another Vercel instance may have initialized the same empty
                # database at the same time. Keep the successful transaction
                # and let the next request use the already-created records.
                db.session.rollback()


@app.context_processor
def global_flags():
    pending_count = 0
    if current_user.is_authenticated and current_user.role in ("admin", "librarian"):
        pending_count = BookRequest.query.filter_by(status="pending").count()
    return {"pending_request_count": pending_count}


@app.errorhandler(ValueError)
def handle_value_error(e):
    if wants_json():
        return json_error(str(e))
    flash(str(e), "danger")
    return redirect(request.referrer or url_for("dashboard"))


# Vercel imports app.py instead of executing it as __main__. Initialize the
# external database during import so tables and demo data exist on first deploy.
# With DATABASE_URL unset locally, this uses the normal library.db SQLite file.
init_db()

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
