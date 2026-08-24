"""Company, location and counter — who is billing, from where, at which till.

A bill has to say who raised it. Until now the shop had exactly one answer, in
config: SHOP_NAME and SHOP_GSTIN, printed on every invoice. That is right for one
company trading from one room, and wrong the moment there are two — two
registrations file two returns, and a month's sales that cannot be split between
them is a month's work to unpick.

So the entity is picked at the till and remembered on the invoice. Three levels,
because they answer three different questions:

    Company   whose GSTIN is on the bill        → prints, and splits the returns
    Location  which branch it was sold from     → the warehouse's own list
    Counter   which till at that branch         → whose drawer it belongs in

LOCATIONS ARE NOT A SECOND MASTER. The warehouse already knows these places: it
dispatches stock to them by name, and its transport register names the branch a
consignment is forwarded to. A shop keeping its own spelling of the same branch
cannot be asked the one question worth asking across the two halves — what did we
send there, and what did they sell. So the names are read from the warehouse and
kept in step, exactly as app/master_categories does with the category master.

Nothing here writes to the warehouse. It is read, as everything else in this shop
reads it: through the engine app/warehouse_items already builds, which knows how
to reach a SQLite file on a warehouse PC and a Postgres database on a deployment.
"""
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import Company, Counter, Location

#: The counter every location starts with. A shop with one till should not have
#: to invent a name for it before it can bill anything.
DEFAULT_COUNTER = "Counter 1"

#: Names that are not places. The warehouse's location list carries NONE as "do
#: not forward this consignment anywhere", which is an answer, not a branch.
NOT_A_PLACE = {"", "none", "-none-", "n/a", "na", "nil"}


def default_company():
    """The company a till bills as before anybody chooses one.

    Seeded from the shop's own config, so an install that has always had one
    company keeps printing exactly what it printed before and never meets the
    picker as a decision. A second company is then somebody adding one, which is
    the moment the choice becomes real.
    """
    row = Company.query.filter_by(is_default=True).first()
    if row:
        return row
    row = Company.query.order_by(Company.id).first()
    if row:
        return row
    cfg = current_app.config
    row = Company(
        name=cfg.get("SHOP_NAME") or "Shop",
        gstin=cfg.get("SHOP_GSTIN") or None,
        address=cfg.get("SHOP_ADDRESS") or None,
        state_code=cfg.get("SHOP_STATE_CODE") or None,
        phone=cfg.get("SHOP_PHONE") or None,
        is_default=True, active=True)
    db.session.add(row)
    db.session.commit()
    return row


def _warehouse_locations():
    """Branch names the warehouse knows, or [] if it cannot be reached.

    The MASTER list only — the one somebody maintains, behind LR Entry's
    auto-transfer branch. Not the destinations stock has actually been dispatched
    to, and that is a deliberate refusal: `to_destination` is free text and the
    warehouse dispatches to customers as well as to branches. A delivery to a
    person would become a "branch" here, and then transfers.sync_transfers would
    take those pieces into shop stock — inventing goods on a shelf out of goods
    that went out of the door.

    The cost of being strict is a branch that has been shipped to but never added
    to the master: its transfers are left alone until somebody adds it. That is
    the right way round. Stock that has not arrived anywhere is a question
    somebody asks; stock that arrived somewhere it never went is a figure nobody
    questions.
    """
    from app import warehouse_items as wh
    con = wh._connect()
    if con is None:
        return []
    names = []
    try:
        # rows come back as dicts keyed by column name — see warehouse_items._Result
        sql = ("SELECT value FROM master_options "
               "WHERE kind = 'auto_transfer_location'")
        try:
            names += [r["value"] for r in con.execute(sql).fetchall() if r.get("value")]
        except SQLAlchemyError:
            pass                  # an older warehouse without that table
    finally:
        con.close()
    seen, out = set(), []
    for n in names:
        n = " ".join(str(n).split())
        if n.lower() in NOT_A_PLACE or n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
    return sorted(out)


def sync_locations():
    """Bring the warehouse's places in, additively, and give each one a till.

    Additive on purpose. A name the warehouse no longer lists is left alone if
    anything is filed under it — a branch that closed still has last year's bills
    against it, and deleting the row to tidy a dropdown would orphan them. Only an
    unused, warehouse-sourced row is retired, and even then by deactivating it
    rather than deleting it.

    Returns (added, retired) for the caller to report or ignore.
    """
    known = _warehouse_locations()
    if not known:
        return 0, 0

    company = default_company()
    have = {loc.name.lower(): loc for loc in Location.query.all()}
    added = 0
    for name in known:
        row = have.get(name.lower())
        if row is None:
            row = Location(name=name, company_id=company.id, local=False, active=True)
            db.session.add(row)
            db.session.flush()
            added += 1
        elif not row.active:
            row.active = True         # it is on the warehouse's list again
        if not Counter.query.filter_by(location_id=row.id).first():
            db.session.add(Counter(name=DEFAULT_COUNTER, location_id=row.id))

    retired = 0
    lowered = {n.lower() for n in known}
    for loc in Location.query.filter_by(local=False, active=True).all():
        if loc.name.lower() in lowered:
            continue
        from app.models import Invoice
        if Invoice.query.filter_by(location_id=loc.id).first():
            continue                  # bills are filed under it; it stays
        loc.active = False
        retired += 1

    db.session.commit()
    return added, retired


def picker_options():
    """Everything the till's Company / Location / Counter dialog draws itself from."""
    return {
        "companies": [{"id": c.id, "name": c.name, "gstin": c.gstin or ""}
                      for c in Company.query.filter_by(active=True)
                                            .order_by(Company.name).all()],
        "locations": [{"id": l.id, "name": l.name, "company_id": l.company_id}
                      for l in Location.query.filter_by(active=True)
                                             .order_by(Location.name).all()],
        "counters": [{"id": x.id, "name": x.name, "location_id": x.location_id}
                     for x in Counter.query.filter_by(active=True)
                                           .order_by(Counter.name).all()],
    }


def resolve(company_id=None, location_id=None, counter_id=None):
    """The three rows a till has chosen, each None if it has not chosen one.

    A counter that does not belong to the chosen location is dropped rather than
    kept, and so is a location that does not belong to the chosen company: a
    till that reads "ESSA GARMENTS / TIRUPUR / Counter 2" where Counter 2 is at
    another branch is worse than one that reads nothing, because it looks
    answered.
    """
    company = db.session.get(Company, company_id) if company_id else None
    location = db.session.get(Location, location_id) if location_id else None
    counter = db.session.get(Counter, counter_id) if counter_id else None
    if location is not None and company is not None and location.company_id \
            and location.company_id != company.id:
        location, counter = None, None
    if counter is not None and (location is None or counter.location_id != location.id):
        counter = None
    return company, location, counter
