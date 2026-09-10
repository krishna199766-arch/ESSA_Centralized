"""Sales promotion schemes — the master the admin keeps, and what it did.

The screens here write scheme rows and nothing else. No billing logic lives in
this file: the engine in app/promotions.py reads what is saved and the till
applies it, so an offer nobody has coded for is an offer somebody typed in.

CONDITIONS AND REWARDS ARRIVE AS JSON, not as `condition_1_qty` form fields.
They are lists of unknown length holding lists of unknown length — a scheme can
name three categories to qualify and four products to reward — and the indexed
form-field encoding for that is a parser nobody can read against a page nobody
can debug. The rows are built in the browser, posted as one field, and validated
here on the way in; anything malformed is refused with the reason rather than
half-saved.
"""
import json
from datetime import date, datetime

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db, promotions
from app.master_categories import grouped_categories
from app.models import (Category, Company, Counter, Location, Product,
                        PromotionApplication, PromotionAudit,
                        PromotionCondition, PromotionConditionItem,
                        PromotionPlace, PromotionReward, PromotionRewardItem,
                        PromotionScheme, OUT_OF_STOCK_ACTIONS, RETURN_POLICIES,
                        REWARD_SELECTION, SCHEME_TYPES)
from app.utils import generate_number, role_required

promotions_bp = Blueprint("promotions", __name__)


class Invalid(ValueError):
    """A scheme that would not mean anything, with the reason it wouldn't."""


# --------------------------------------------------------------------------
# reading the form
# --------------------------------------------------------------------------

def _date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise Invalid(f"“{raw}” is not a date the form can read (YYYY-MM-DD).")


def _rows(raw, what):
    """Parse one of the JSON fields into a list of dicts, or say why not."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        raise Invalid(f"The {what} could not be read.")
    if not isinstance(rows, list):
        raise Invalid(f"The {what} could not be read.")
    return rows


def _targets(rows, what):
    """[(product_id, category_id), …] from the picker's rows.

    Each row names EITHER a product or a category. Both, or neither, is a row
    that cannot be matched against, so it is refused rather than stored as
    something the engine will silently skip.
    """
    out = []
    for r in rows or []:
        pid = r.get("product_id") or None
        cid = r.get("category_id") or None
        pid = int(pid) if pid else None
        cid = int(cid) if cid else None
        if bool(pid) == bool(cid):
            raise Invalid(f"Each {what} row must name one product or one "
                          f"category — not both, and not neither.")
        if pid and not db.session.get(Product, pid):
            raise Invalid(f"No product #{pid} — it may have been archived.")
        if cid and not db.session.get(Category, cid):
            raise Invalid(f"No category #{cid}.")
        out.append((pid, cid))
    if not out:
        raise Invalid(f"A scheme needs at least one {what}.")
    return out


def _read_form(form):
    """Everything the form said, validated. Raises Invalid with the reason."""
    name = (form.get("name") or "").strip()
    if not name:
        raise Invalid("The scheme needs a name.")

    scheme_type = (form.get("scheme_type") or "buy_get_free").strip()
    if scheme_type not in SCHEME_TYPES:
        raise Invalid(f"“{scheme_type}” is not a scheme type.")

    start = _date(form.get("start_date"))
    end = _date(form.get("end_date"))
    if start and end and end < start:
        raise Invalid("The scheme ends before it starts.")

    conditions = _rows(form.get("conditions_json"), "buy rules")
    if not conditions:
        raise Invalid("A scheme needs at least one thing the customer has to buy.")
    parsed_conditions = []
    for c in conditions:
        try:
            qty = float(c.get("min_qty") or 0)
        except (TypeError, ValueError):
            raise Invalid("A buy quantity must be a number.")
        if qty <= 0:
            raise Invalid("A buy quantity must be more than zero.")
        parsed_conditions.append({
            "min_qty": qty,
            "label": (c.get("label") or "").strip()[:128] or None,
            "targets": _targets(c.get("items"), "qualifying product"),
        })

    rewards = _rows(form.get("rewards_json"), "reward rules")
    if not rewards:
        raise Invalid("A scheme needs at least one reward.")
    parsed_rewards = []
    for r in rewards:
        try:
            qty = float(r.get("qty") or 0)
            value = float(r.get("value") or 0)
        except (TypeError, ValueError):
            raise Invalid("A reward quantity must be a number.")
        if qty <= 0:
            raise Invalid("A reward quantity must be more than zero.")
        if scheme_type == "buy_get_percent" and not 0 < value <= 100:
            raise Invalid("A percentage reward needs a discount between 0 and 100.")
        if scheme_type == "buy_get_price" and value < 0:
            raise Invalid("A fixed-price reward cannot be negative.")
        parsed_rewards.append({
            "qty": qty, "value": value,
            "targets": _targets(r.get("items"), "reward product"),
        })

    places = []
    for p in _rows(form.get("places_json"), "locations"):
        row = {k: (int(p[k]) if p.get(k) else None)
               for k in ("warehouse_id", "company_id", "location_id", "counter_id")}
        if any(row.values()):
            places.append(row)

    max_apps = (form.get("max_applications") or "").strip()
    try:
        max_apps = int(max_apps) if max_apps else None
    except ValueError:
        raise Invalid("The maximum number of applications must be a whole number.")
    if max_apps is not None and max_apps < 1:
        raise Invalid("A maximum of zero applications would switch the scheme off "
                      "— untick Active instead, so it is clear what happened.")

    try:
        priority = int((form.get("priority") or "100").strip() or 100)
    except ValueError:
        raise Invalid("Priority must be a whole number.")

    selection = (form.get("reward_selection") or "cheapest").strip()
    oos = (form.get("out_of_stock") or "block").strip()
    ret = (form.get("return_policy") or "reclaim_value").strip()
    for value, allowed, what in ((selection, REWARD_SELECTION, "reward choice"),
                                 (oos, OUT_OF_STOCK_ACTIONS, "out-of-stock rule"),
                                 (ret, RETURN_POLICIES, "return rule")):
        if value not in allowed:
            raise Invalid(f"“{value}” is not a {what}.")

    return {
        "name": name, "scheme_type": scheme_type,
        "code": (form.get("code") or "").strip()[:32] or None,
        "start_date": start, "end_date": end,
        "active": bool(form.get("active")),
        "stackable": bool(form.get("stackable")),
        "priority": priority, "max_applications": max_apps,
        "reward_selection": selection, "out_of_stock": oos, "return_policy": ret,
        "terms": (form.get("terms") or "").strip() or None,
        "conditions": parsed_conditions, "rewards": parsed_rewards,
        "places": places,
    }


def _write(scheme, parsed):
    """Put a validated form onto a scheme row, replacing its rules wholesale.

    Replaced rather than merged: a rule that was deleted on screen has to be
    gone, and matching up rows that have no stable identity between one edit and
    the next is a reconciliation problem invented for nothing. The applications
    already recorded against the scheme are untouched — they carry their own copy
    of what the customer was given (see PromotionApplication).
    """
    for field in ("name", "scheme_type", "start_date", "end_date", "active",
                  "stackable", "priority", "max_applications",
                  "reward_selection", "out_of_stock", "return_policy", "terms"):
        setattr(scheme, field, parsed[field])

    # The columns FIRST, then the row, then the rules that point at it. A new
    # scheme is not insertable until its name is set — and every query between
    # `add` and now would autoflush it as it stands, which is how this route
    # first tried to write a scheme called nothing.
    if scheme not in db.session:
        db.session.add(scheme)
    scheme.conditions.clear()
    scheme.rewards.clear()
    scheme.places.clear()
    db.session.flush()

    for i, c in enumerate(parsed["conditions"]):
        cond = PromotionCondition(scheme_id=scheme.id, min_qty=c["min_qty"],
                                  label=c["label"], sort_order=i)
        db.session.add(cond)
        db.session.flush()
        for pid, cid in c["targets"]:
            db.session.add(PromotionConditionItem(
                condition_id=cond.id, product_id=pid, category_id=cid))

    for i, r in enumerate(parsed["rewards"]):
        rew = PromotionReward(scheme_id=scheme.id, qty=r["qty"],
                              value=r["value"], sort_order=i)
        db.session.add(rew)
        db.session.flush()
        for pid, cid in r["targets"]:
            db.session.add(PromotionRewardItem(
                reward_id=rew.id, product_id=pid, category_id=cid))

    for p in parsed["places"]:
        db.session.add(PromotionPlace(scheme_id=scheme.id, **p))


def _form_context():
    """Everything both the new and the edit form need to draw their pickers."""
    return {
        "category_groups": grouped_categories(),
        "companies": Company.query.filter_by(active=True).order_by(Company.name).all(),
        "locations": Location.query.filter_by(active=True).order_by(Location.name).all(),
        "counters": (db.session.query(Counter, Location.name)
                     .join(Location, Location.id == Counter.location_id)
                     .filter(Counter.active.is_(True))
                     .order_by(Location.name, Counter.name).all()),
        "scheme_types": SCHEME_TYPES,
        "reward_selection": REWARD_SELECTION,
        "out_of_stock_actions": OUT_OF_STOCK_ACTIONS,
        "return_policies": RETURN_POLICIES,
        "today": date.today().isoformat(),
    }


def _as_json(scheme):
    """A saved scheme in the shape the form's JavaScript builds rows from."""
    def targets(rows):
        return [{"product_id": r.product_id, "category_id": r.category_id,
                 "label": r.label} for r in rows]
    return {
        "conditions": [{"min_qty": c.min_qty, "label": c.label or "",
                        "items": targets(c.items)} for c in scheme.conditions],
        "rewards": [{"qty": r.qty, "value": r.value, "items": targets(r.items)}
                    for r in scheme.rewards],
        "places": [{"warehouse_id": p.warehouse_id, "company_id": p.company_id,
                    "location_id": p.location_id, "counter_id": p.counter_id,
                    "label": p.label} for p in scheme.places],
    }


# --------------------------------------------------------------------------
# screens
# --------------------------------------------------------------------------

@promotions_bp.route("/")
@login_required
@role_required("admin", "manager")
def index():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()

    query = PromotionScheme.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(PromotionScheme.name.ilike(like),
                                    PromotionScheme.code.ilike(like)))
    schemes = query.order_by(PromotionScheme.priority, PromotionScheme.id).all()

    # Status is derived (see PromotionScheme.status), so it is filtered here
    # rather than in SQL — there is no column to compare against, and inventing
    # one would be a second answer to a question the dates already settle.
    today = date.today()
    counts = {"active": 0, "scheduled": 0, "expired": 0, "inactive": 0}
    for s in schemes:
        counts[s.status(today)] += 1
    if status in counts:
        schemes = [s for s in schemes if s.status(today) == status]

    # One grouped query rather than a walk of each scheme's applications: the
    # list draws a benefit figure per row, and a shop running a dozen offers
    # would otherwise fire a dozen queries to do it.
    used = dict(db.session.query(PromotionApplication.scheme_id,
                                 func.count(PromotionApplication.id))
                .group_by(PromotionApplication.scheme_id).all())
    given = dict(db.session.query(PromotionApplication.scheme_id,
                                  func.coalesce(func.sum(
                                      PromotionApplication.benefit_value), 0))
                 .group_by(PromotionApplication.scheme_id).all())

    return render_template("promotions/list.html", schemes=schemes, q=q,
                           status=status, counts=counts, used=used, given=given,
                           describe=promotions.describe, today=today)


@promotions_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "manager")
def new_scheme():
    if request.method == "POST":
        try:
            parsed = _read_form(request.form)
        except Invalid as exc:
            flash(str(exc), "danger")
            return render_template("promotions/form.html", scheme=None,
                                   saved=request.form, rules="{}",
                                   **_form_context())
        # Settled before anything is put in the session, because both of these
        # are queries and a query autoflushes whatever is pending.
        code = parsed["code"] or generate_number("PROMO", PromotionScheme, "code")
        if PromotionScheme.query.filter_by(code=code).first():
            flash(f"Scheme code {code} is already in use.", "danger")
            return render_template("promotions/form.html", scheme=None,
                                   saved=request.form, rules="{}",
                                   **_form_context())
        scheme = PromotionScheme(code=code, created_by_id=current_user.id,
                                 updated_by_id=current_user.id)
        _write(scheme, parsed)
        db.session.commit()
        flash(f"Scheme {scheme.code} created — {promotions.describe(scheme)}.",
              "success")
        return redirect(url_for("promotions.detail", sid=scheme.id))

    return render_template("promotions/form.html", scheme=None, saved={},
                           rules="{}", **_form_context())


@promotions_bp.route("/<int:sid>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "manager")
def edit_scheme(sid):
    scheme = PromotionScheme.query.get_or_404(sid)
    if request.method == "POST":
        try:
            parsed = _read_form(request.form)
        except Invalid as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("promotions/form.html", scheme=scheme,
                                   saved=request.form,
                                   rules=json.dumps(_as_json(scheme)),
                                   **_form_context())
        if parsed["code"] and parsed["code"] != scheme.code:
            clash = PromotionScheme.query.filter_by(code=parsed["code"]).first()
            if clash and clash.id != scheme.id:
                flash(f"Scheme code {parsed['code']} is already in use.", "danger")
                return render_template("promotions/form.html", scheme=scheme,
                                       saved=request.form,
                                       rules=json.dumps(_as_json(scheme)),
                                       **_form_context())
            scheme.code = parsed["code"]
        scheme.updated_by_id = current_user.id
        _write(scheme, parsed)
        db.session.commit()
        flash("Scheme updated.", "success")
        return redirect(url_for("promotions.detail", sid=scheme.id))

    return render_template("promotions/form.html", scheme=scheme, saved={},
                           rules=json.dumps(_as_json(scheme)), **_form_context())


@promotions_bp.route("/<int:sid>")
@login_required
@role_required("admin", "manager")
def detail(sid):
    scheme = PromotionScheme.query.get_or_404(sid)
    apps = (PromotionApplication.query.filter_by(scheme_id=sid)
            .order_by(PromotionApplication.id.desc()).limit(200).all())
    trail = (PromotionAudit.query.filter_by(scheme_id=sid)
             .order_by(PromotionAudit.id.desc()).limit(200).all())
    summary = {
        "bills": len({a.invoice_id for a in apps}),
        "times": sum(a.times_applied or 0 for a in apps),
        "qualifying": round(sum(a.qualifying_qty or 0 for a in apps), 3),
        "free": round(sum(a.reward_qty or 0 for a in apps), 3),
        "benefit": round(sum(a.benefit_value or 0 for a in apps), 2),
        # The ones nobody got, which is the number that says an offer is
        # advertised and undeliverable.
        "blocked": sum(1 for t in trail if t.event == "blocked_no_stock"),
    }
    return render_template("promotions/detail.html", scheme=scheme, apps=apps,
                           trail=trail, summary=summary,
                           description=promotions.describe(scheme),
                           today=date.today())


@promotions_bp.route("/<int:sid>/toggle", methods=["POST"])
@login_required
@role_required("admin", "manager")
def toggle(sid):
    scheme = PromotionScheme.query.get_or_404(sid)
    scheme.active = not scheme.active
    scheme.updated_by_id = current_user.id
    db.session.commit()
    flash(f"{scheme.code} is now {'active' if scheme.active else 'inactive'}.",
          "success" if scheme.active else "info")
    return redirect(request.referrer or url_for("promotions.index"))


@promotions_bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete(sid):
    """Remove a scheme that never ran. One that has is deactivated instead.

    A scheme with bills against it is a record of goods that left the shop, and
    deleting it would leave those applications pointing at nothing — the free
    garments would still be gone, with no offer to explain them. So the button
    changes its meaning rather than refusing: switched off, kept, still
    reportable.
    """
    scheme = PromotionScheme.query.get_or_404(sid)
    if PromotionApplication.query.filter_by(scheme_id=sid).first():
        scheme.active = False
        scheme.updated_by_id = current_user.id
        db.session.commit()
        flash(f"{scheme.code} has been used on bills, so it was deactivated "
              f"rather than deleted — its history stays reportable.", "warning")
        return redirect(url_for("promotions.detail", sid=sid))
    PromotionAudit.query.filter_by(scheme_id=sid).delete()
    db.session.delete(scheme)
    db.session.commit()
    flash("Scheme deleted — it had never been applied to a bill.", "info")
    return redirect(url_for("promotions.index"))


# --------------------------------------------------------------------------
# the pickers
# --------------------------------------------------------------------------

@promotions_bp.route("/api/products")
@login_required
@role_required("admin", "manager")
def api_products():
    """Search the product master for the qualifying / reward pickers.

    A search rather than a dropdown: the catalogue runs to thousands of variants
    — one product per size and colour — and a select carrying all of them is a
    page nobody can use and a payload nobody should send.
    """
    q = (request.args.get("q") or "").strip()
    query = Product.query.filter(Product.active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Product.name.ilike(like),
                                    Product.sku.ilike(like),
                                    Product.barcode.ilike(like)))
    rows = query.order_by(Product.name).limit(40).all()
    return jsonify([{"id": p.id, "sku": p.sku, "name": p.name,
                     "price": p.selling_price, "stock": p.stock_qty,
                     "category": p.category.name if p.category else ""}
                    for p in rows])


@promotions_bp.route("/api/preview", methods=["POST"])
@login_required
@role_required("admin", "manager")
def api_preview():
    """Try a cart against the live schemes without billing anything.

    An offer that reads correctly on the form and gives nothing at the till is
    the whole reason this exists — a category that holds no stock, a priority
    that lets an earlier scheme take the garments first. This answers it before
    a customer does.
    """
    data = request.get_json(silent=True) or {}
    outcome = promotions.evaluate(data.get("items") or [],
                                  choices=data.get("choices") or {})
    return jsonify(outcome.to_json())
