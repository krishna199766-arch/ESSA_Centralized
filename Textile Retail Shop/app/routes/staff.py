from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func
from app import db
from app.models import User, Attendance, CreditNote, Invoice
from app.utils import role_required

staff_bp = Blueprint("staff", __name__)


@staff_bp.route("/")
@login_required
@role_required("admin", "manager")
def list_staff():
    staff = User.query.order_by(User.full_name).all()
    return render_template("staff/list.html", staff=staff)


@staff_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def new_staff():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"].strip()).first()
        if u:
            flash("Username already exists.", "danger")
        else:
            u = User(
                username=request.form["username"].strip(),
                full_name=request.form["full_name"].strip(),
                email=request.form.get("email", ""),
                phone=request.form.get("phone", ""),
                role=request.form.get("role", "cashier"),
                salary=float(request.form.get("salary") or 0),
                commission_pct=float(request.form.get("commission_pct") or 0),
            )
            u.set_password(request.form.get("password") or "changeme")
            db.session.add(u)
            db.session.commit()
            flash("Staff added.", "success")
            return redirect(url_for("staff.list_staff"))
    return render_template("staff/form.html", staff=None)


@staff_bp.route("/<int:uid>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_staff(uid):
    u = User.query.get_or_404(uid)
    if request.method == "POST":
        u.full_name = request.form["full_name"].strip()
        u.email = request.form.get("email", "")
        u.phone = request.form.get("phone", "")
        u.role = request.form.get("role", u.role)
        u.salary = float(request.form.get("salary") or 0)
        u.commission_pct = float(request.form.get("commission_pct") or 0)
        u.active = bool(request.form.get("active"))
        pw = request.form.get("password")
        if pw:
            u.set_password(pw)
        db.session.commit()
        flash("Staff updated.", "success")
        return redirect(url_for("staff.list_staff"))
    return render_template("staff/form.html", staff=u)


@staff_bp.route("/attendance")
@login_required
@role_required("admin", "manager")
def attendance():
    today = date.today()
    month_ago = today - timedelta(days=30)
    records = Attendance.query.filter(Attendance.check_in >= month_ago).order_by(Attendance.check_in.desc()).all()
    return render_template("staff/attendance.html", records=records)


@staff_bp.route("/<int:uid>/card")
@login_required
@role_required("admin", "manager")
def staff_card(uid):
    """Printable ID card. Its QR is the staff code the billing counter reads."""
    u = User.query.get_or_404(uid)
    return render_template("staff/card.html", staff=u)


@staff_bp.route("/commissions")
@login_required
@role_required("admin", "manager")
def commissions():
    today = date.today()
    month_start = today.replace(day=1)
    rows = []
    for u in User.query.filter(User.active == True).all():
        # Credit the person who served the sale. `staff_id` is what the counter
        # records once the staff member has been identified; invoices raised
        # before that existed have none, and fall back to the till login so the
        # older figures don't quietly drop to zero.
        served_by_them = db.or_(
            Invoice.staff_id == u.id,
            db.and_(Invoice.staff_id.is_(None), Invoice.cashier_id == u.id),
        )
        sold = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
            served_by_them,
            func.date(Invoice.invoice_date) >= month_start
        ).scalar() or 0

        # Goods that came back are goods nobody sold. The credit is taken off the
        # staff member who made the ORIGINAL sale, not whoever handled the return
        # — otherwise processing a refund would cost you your own commission.
        returned = db.session.query(func.coalesce(func.sum(CreditNote.total), 0)).join(
            Invoice, CreditNote.invoice_id == Invoice.id
        ).filter(
            served_by_them,
            func.date(CreditNote.created_at) >= month_start
        ).scalar() or 0

        sales = round(sold - returned, 2)
        commission = round(sales * u.commission_pct / 100.0, 2)
        rows.append({"user": u, "sales": sales, "returned": returned,
                     "commission": commission})
    return render_template("staff/commissions.html", rows=rows, month_start=month_start)
