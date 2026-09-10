"""Floors and tills — the master that decides what a bill is called.

Configuration, not code. The brief names four storeys and four prefixes for one
shop; what is built here is a table of storeys with prefixes on it, so a fifth
floor, a second store, or a different set of letters is somebody typing rather
than somebody deploying.

Two things are deliberately NOT editable here.

**Stores** come from the warehouse and are kept in step with it (see
app/places.sync_locations) — a shop that renamed its own copy of a branch could
not be asked what was sent there and what sold. So this screen lists them and
hangs floors off them.

**A prefix on a bill that has already been raised.** Changing a floor's prefix
changes what its NEXT bill is called and nothing else; every bill already
printed keeps the number it went out with, and keeps its own copy of the prefix
and sequence it was built from (see Invoice.bill_prefix). A master that rewrote
history to tidy itself up would be a master that loses the audit.
"""
from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import login_required
from sqlalchemy import func

from app import billing_numbers, db, places
from app.models import BillSequence, Counter, Floor, Invoice, Location
from app.utils import role_required

stores_bp = Blueprint("stores", __name__)


def _clean_prefix(raw):
    """A prefix as it will be stored: letters and digits, upper case, short.

    Anything else is refused rather than stripped quietly — a prefix with a space
    or a dash in it makes a bill number nobody can parse back, and the series
    lookup that finds a shop's existing numbers works by matching exactly this
    string.
    """
    value = (raw or "").strip().upper()
    if not value:
        raise ValueError("A floor needs a bill prefix — TG, TF and so on.")
    if not value.isalnum():
        raise ValueError(f"“{value}” cannot be a bill prefix: letters and "
                         f"digits only, so the number reads back cleanly.")
    if len(value) > 8:
        raise ValueError("A bill prefix is at most 8 characters.")
    return value


@stores_bp.route("/")
@login_required
@role_required("admin", "manager")
def index():
    """Every store, its storeys, and which till stands on which.

    One page rather than three, because the only question anybody comes here with
    is "what will this till's bills be called", and that answer is spread across
    all three levels.
    """
    locations = Location.query.filter_by(active=True).order_by(Location.name).all()

    # Bills raised per floor this financial year, so a floor that is configured
    # and never used is visible as such — a till mapped to the wrong storey shows
    # up here as a series nobody is billing on.
    year = billing_numbers.financial_year()
    counts = dict(db.session.query(Invoice.floor_id, func.count(Invoice.id))
                  .filter(Invoice.floor_id.isnot(None),
                          Invoice.fin_year == year)
                  .group_by(Invoice.floor_id).all())
    series = {(s.prefix, s.fin_year): s.last_number
              for s in BillSequence.query.all()}

    # A prefix shared by two floors is legal and means one register between them
    # — but it is almost always a typo, so it is counted here and said out loud
    # on the screen rather than discovered in a month's bills.
    shared = {}
    for f in Floor.query.filter_by(active=True).all():
        shared.setdefault(f.prefix, []).append(f)

    return render_template(
        "stores/index.html", locations=locations, counts=counts, series=series,
        year=year, shared={k: v for k, v in shared.items() if len(v) > 1},
        standard=billing_numbers.STANDARD_FLOORS,
        unassigned=Counter.query.filter(Counter.floor_id.is_(None),
                                        Counter.active.is_(True)).count(),
        example=billing_numbers.format_number("TG", year, 1))


@stores_bp.route("/floors/new", methods=["POST"])
@login_required
@role_required("admin", "manager")
def new_floor():
    location = Location.query.get_or_404(request.form.get("location_id", type=int))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A floor needs a name.", "danger")
        return redirect(url_for("stores.index"))
    try:
        prefix = _clean_prefix(request.form.get("prefix"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("stores.index"))
    if Floor.query.filter_by(location_id=location.id, name=name).first():
        flash(f"{location.name} already has a floor called “{name}”.", "warning")
        return redirect(url_for("stores.index"))

    db.session.add(Floor(location_id=location.id, name=name, prefix=prefix,
                         sort_order=request.form.get("sort_order", type=int) or 0,
                         active=True))
    db.session.commit()
    flash(f"{name} added at {location.name} — its bills will be "
          f"{billing_numbers.format_number(prefix, billing_numbers.financial_year(), 1)}.",
          "success")
    return redirect(url_for("stores.index"))


@stores_bp.route("/floors/standard", methods=["POST"])
@login_required
@role_required("admin", "manager")
def standard_floors():
    """Add the four storeys the brief names, for a store that has none.

    A convenience on the screen, not a rule in the engine: it writes exactly the
    rows somebody would have typed, and nothing in the billing path knows this
    button exists. A store that needs five floors, or two, adds them one at a
    time above.
    """
    location = Location.query.get_or_404(request.form.get("location_id", type=int))
    made = []
    for order, (name, prefix) in enumerate(billing_numbers.STANDARD_FLOORS):
        if Floor.query.filter_by(location_id=location.id, name=name).first():
            continue
        db.session.add(Floor(location_id=location.id, name=name, prefix=prefix,
                             sort_order=order, active=True))
        made.append(f"{name} ({prefix})")
    db.session.commit()
    flash(f"Added at {location.name}: {', '.join(made)}." if made
          else f"{location.name} already has those floors.",
          "success" if made else "info")
    return redirect(url_for("stores.index"))


@stores_bp.route("/floors/<int:fid>", methods=["POST"])
@login_required
@role_required("admin", "manager")
def edit_floor(fid):
    storey = Floor.query.get_or_404(fid)
    name = (request.form.get("name") or "").strip()
    try:
        prefix = _clean_prefix(request.form.get("prefix"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("stores.index"))
    if not name:
        flash("A floor needs a name.", "danger")
        return redirect(url_for("stores.index"))

    if prefix != storey.prefix:
        # Said plainly, because this is the one edit here with a consequence
        # somebody might not expect: the new series starts at its own 001, and
        # every bill already raised keeps the number it was printed with.
        used = Invoice.query.filter_by(floor_id=storey.id).count()
        if used:
            flash(f"{storey.name} moves from {storey.prefix} to {prefix} for its "
                  f"NEXT bill. The {used} bill(s) already raised keep the numbers "
                  f"they went out with.", "warning")
    storey.name = name
    storey.prefix = prefix
    storey.sort_order = request.form.get("sort_order", type=int) or 0
    storey.active = bool(request.form.get("active"))
    db.session.commit()
    flash(f"{storey.name} updated.", "success")
    return redirect(url_for("stores.index"))


@stores_bp.route("/counters/<int:cid>/floor", methods=["POST"])
@login_required
@role_required("admin", "manager")
def assign_counter(cid):
    """Put a till on a storey — the mapping the bill prefix comes from."""
    till = Counter.query.get_or_404(cid)
    raw = request.form.get("floor_id")
    if not raw:
        till.floor_id = None
        db.session.commit()
        flash(f"{till.name} is no longer on a floor — it bills on the shop's "
              f"plain {billing_numbers.FALLBACK_PREFIX}- series.", "info")
        return redirect(url_for("stores.index"))

    storey = Floor.query.get_or_404(int(raw))
    if storey.location_id != till.location_id:
        # A till cannot stand in another building. Refused rather than allowed
        # and explained, because the whole point of the mapping is that the
        # prefix says where the sale happened.
        flash(f"{storey.name} is at {storey.location.name}, and {till.name} is "
              f"at {till.location.name}. A till can only be on a floor of its "
              f"own store.", "danger")
        return redirect(url_for("stores.index"))
    till.floor_id = storey.id
    db.session.commit()
    flash(f"{till.name} is on {storey.name} — its bills will read "
          f"{billing_numbers.format_number(storey.prefix, billing_numbers.financial_year(), 1)}.",
          "success")
    return redirect(url_for("stores.index"))


@stores_bp.route("/counters/new", methods=["POST"])
@login_required
@role_required("admin", "manager")
def new_counter():
    """A second till on a floor. One drawer each — see models.Counter."""
    location = Location.query.get_or_404(request.form.get("location_id", type=int))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A till needs a name.", "danger")
        return redirect(url_for("stores.index"))
    if Counter.query.filter_by(location_id=location.id, name=name).first():
        flash(f"{location.name} already has a till called “{name}”.", "warning")
        return redirect(url_for("stores.index"))
    floor_id = request.form.get("floor_id", type=int)
    storey = Floor.query.get(floor_id) if floor_id else None
    if storey is not None and storey.location_id != location.id:
        storey = None
    db.session.add(Counter(name=name, location_id=location.id,
                           floor_id=storey.id if storey else None, active=True))
    db.session.commit()
    flash(f"{name} added at {location.name}"
          + (f", on {storey.name}." if storey else "."), "success")
    return redirect(url_for("stores.index"))


@stores_bp.route("/series")
@login_required
@role_required("admin", "manager")
def series():
    """Every bill series and where it has got to.

    The register behind the register: one row per prefix and financial year,
    with the last number handed out. It is what somebody checks when they want to
    know that a floor's numbering is where they expect it to be, and it is read
    only — the next number is taken by billing a sale, never by editing this.
    """
    rows = BillSequence.query.order_by(BillSequence.fin_year.desc(),
                                       BillSequence.prefix).all()
    floors = {}
    for f in Floor.query.all():
        floors.setdefault(f.prefix, []).append(f)
    counts = dict(db.session.query(Invoice.bill_prefix, func.count(Invoice.id))
                  .group_by(Invoice.bill_prefix).all())
    return render_template("stores/series.html", rows=rows, floors=floors,
                           counts=counts, year=billing_numbers.financial_year(),
                           fallback=billing_numbers.FALLBACK_PREFIX)
