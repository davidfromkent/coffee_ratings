from fastapi import FastAPI, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from .dependencies import get_db
from . import models
from .database import engine

# Create tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ---------------------------------------------------------
# Helper: Update all average scores for a venue
# ---------------------------------------------------------
def update_venue_averages(db, venue_id: int):
    reviews = db.query(models.Review).filter(models.Review.venue_id == venue_id).all()

    if not reviews:
        venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()
        if not venue:
            return
        venue.avg_coffee = None
        venue.avg_cost = None
        venue.avg_service = None
        venue.avg_hygiene = None
        venue.avg_ambience = None
        venue.avg_food = None
        venue.avg_total_score = None
        db.commit()
        return

    def safe_avg(values):
        return sum(values) / len(values) if values else None

    avg_coffee = safe_avg([r.coffee for r in reviews])
    avg_cost = safe_avg([r.cost for r in reviews])
    avg_service = safe_avg([r.service for r in reviews])
    avg_hygiene = safe_avg([r.hygiene for r in reviews])
    avg_ambience = safe_avg([r.ambience for r in reviews])
    avg_food = safe_avg([r.food for r in reviews if r.food != 0])

    # Total score is average of each review's average cups
    weighted_scores = [
        r.total_score / r.category_count
        for r in reviews
    ]
    avg_total = sum(weighted_scores) / len(weighted_scores)

    venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()
    venue.avg_coffee = avg_coffee
    venue.avg_cost = avg_cost
    venue.avg_service = avg_service
    venue.avg_hygiene = avg_hygiene
    venue.avg_ambience = avg_ambience
    venue.avg_food = avg_food
    venue.avg_total_score = avg_total

    db.commit()


# ---------------------------------------------------------
# Helper: add or replace ?msg=... on a URL
# ---------------------------------------------------------
def _add_msg(url: str, msg: str) -> str:
    if not url:
        return f"/reviews?msg={msg}"
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["msg"] = msg
    new_query = urlencode(q)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))


# ---------------------------------------------------------
# DELETE REVIEW (owner-only via identity_pin)
# ---------------------------------------------------------
@app.post("/reviews/{review_id}/delete")
def delete_review(
    request: Request,
    review_id: int,
    identity_pin: str = Form(...),
    db: Session = Depends(get_db),
):
    review = db.query(models.Review).filter(models.Review.id == review_id).first()
    referer = request.headers.get("referer") or "/reviews"

    if not review:
        return RedirectResponse(_add_msg(referer, "notfound"), status_code=303)

    if review.identity_pin != identity_pin:
        return RedirectResponse(_add_msg(referer, "denied"), status_code=303)

    venue_id = review.venue_id
    db.delete(review)
    db.commit()

    update_venue_averages(db, venue_id)

    return RedirectResponse(_add_msg(referer, "deleted"), status_code=303)


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "title": "Coffee Ratings",
        },
    )


# ---------------------------------------------------------
# VENUES LIST
# ---------------------------------------------------------
@app.get("/venues", response_class=HTMLResponse)
def list_venues(request: Request, db: Session = Depends(get_db)):
    venues = db.query(models.Venue).all()

    # Venues list is always entered from Home except when coming from Reviews
    back_url = "/"

    return templates.TemplateResponse(
        "venues.html",
        {
            "request": request,
            "venues": venues,
            "title": "Venues",
            "back_url": back_url,
        },
    )


# ---------------------------------------------------------
# VENUE DETAIL
# ---------------------------------------------------------
@app.get("/venues/{venue_id}", response_class=HTMLResponse)
def venue_detail(
    venue_id: int,
    request: Request,
    from_param: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db),
):
    venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()
    reviews = db.query(models.Review).filter(models.Review.venue_id == venue_id).all()

    # Default
    back_url = "/venues"

    # Marker from previous page
    if from_param == "reviews":
        back_url = "/reviews"
    elif from_param == "venues":
        back_url = "/venues"

    return templates.TemplateResponse(
        "venue_detail.html",
        {
            "request": request,
            "venue": venue,
            "reviews": reviews,
            "title": venue.name,
            "back_url": back_url,
            "msg": msg,
        },
    )


# ---------------------------------------------------------
# REVIEWS LIST
# ---------------------------------------------------------
@app.get("/reviews", response_class=HTMLResponse)
def list_reviews(
    request: Request,
    q: Optional[str] = None,
    sort: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Review).join(models.Venue)

    search_query = ""
    if q:
        search_query = q.strip()
        if search_query:
            like = f"%{search_query}%"
            query = query.filter(models.Venue.name.ilike(like))

    if sort == "high":
        query = query.order_by(models.Review.total_score.desc())
    elif sort == "low":
        query = query.order_by(models.Review.total_score.asc())
    elif sort == "new":
        query = query.order_by(models.Review.visit_date.desc())
    else:
        query = query.order_by(models.Review.visit_date.desc())

    reviews = query.all()

    # Back URL for reviews list
    back_url = "/"

    return templates.TemplateResponse(
        "reviews.html",
        {
            "request": request,
            "reviews": reviews,
            "title": "Reviews",
            "search_query": search_query,
            "sort": sort,
            "msg": msg,
            "back_url": back_url,
        },
    )


# ---------------------------------------------------------
# ADD REVIEW (FORM)
# ---------------------------------------------------------
@app.get("/reviews/new", response_class=HTMLResponse)
def new_review_form(
    request: Request,
    venue_id: Optional[int] = None,
    from_param: Optional[str] = None,
    db: Session = Depends(get_db),
):
    venue = None
    if venue_id:
        venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()

    # Back logic
    if venue_id:
        # Add review from venue detail
        if from_param == "reviews":
            back_url = f"/venues/{venue_id}?from=reviews"
        else:
            back_url = f"/venues/{venue_id}?from=venues"
    else:
        # Add review from Home
        back_url = "/"

    return templates.TemplateResponse(
        "new_review.html",
        {
            "request": request,
            "venue": venue,
            "title": "Add Review",
            "back_url": back_url,
        },
    )


# ---------------------------------------------------------
# DUPLICATE CHECK ENDPOINT
# ---------------------------------------------------------
@app.get("/check-duplicate", response_class=HTMLResponse)
def check_duplicate(
    request: Request,
    identity_pin: str,
    venue_id: int,
    visit_date: str,
    db: Session = Depends(get_db),
):
    existing = (
        db.query(models.Review)
        .filter(
            models.Review.identity_pin == identity_pin,
            models.Review.venue_id == venue_id,
            models.Review.visit_date == visit_date,
        )
        .first()
    )

    if existing:
        return templates.TemplateResponse(
            "duplicate_prompt.html",
            {
                "request": request,
                "existing_review": existing,
                "venue_id": venue_id,
                "visit_date": visit_date,
            },
        )

    return HTMLResponse("")


# ---------------------------------------------------------
# ADD REVIEW (SUBMIT)
# ---------------------------------------------------------
@app.post("/reviews/new")
def add_review(
    request: Request,
    venue_name: str = Form(...),
    location: str = Form(...),
    visit_date: str = Form(...),
    reviewer_name: str = Form(...),
    identity_pin: str = Form(...),
    coffee: int = Form(...),
    cost: int = Form(...),
    service: int = Form(...),
    hygiene: int = Form(...),
    ambience: int = Form(...),
    food: int = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    # Get or create venue
    venue = (
        db.query(models.Venue)
        .filter(
            func.lower(models.Venue.name) == venue_name.strip().lower(),
            func.lower(models.Venue.location) == location.strip().lower(),
        )
        .first()
    )

    if not venue:
        venue = models.Venue(name=venue_name.strip(), location=location.strip())
        db.add(venue)
        db.commit()
        db.refresh(venue)

    # Duplicate check
    existing = (
        db.query(models.Review)
        .filter(
            models.Review.identity_pin == identity_pin,
            models.Review.venue_id == venue.id,
            models.Review.visit_date == visit_date,
        )
        .first()
    )

    if existing:
        return templates.TemplateResponse(
            "duplicate_prompt.html",
            {
                "request": request,
                "existing_review": existing,
                "venue_id": venue.id,
                "visit_date": visit_date,
                "form_data": {
                    "venue_name": venue_name,
                    "location": location,
                    "visit_date": visit_date,
                    "reviewer_name": reviewer_name,
                    "identity_pin": identity_pin,
                    "coffee": coffee,
                    "cost": cost,
                    "service": service,
                    "hygiene": hygiene,
                    "ambience": ambience,
                    "food": food,
                    "notes": notes,
                },
            },
        )

    # Create review
    total_score = coffee + cost + service + hygiene + ambience + food
    category_count = 6

    review = models.Review(
        identity_pin=identity_pin.strip(),
        reviewer_name=reviewer_name.strip(),
        venue_id=venue.id,
        venue_name_raw=venue.name,
        venue_location_raw=venue.location,
        visit_date=visit_date,
        coffee=coffee,
        cost=cost,
        service=service,
        hygiene=hygiene,
        ambience=ambience,
        food=food,
        total_score=total_score,
        category_count=category_count,
        notes=notes.strip(),
    )
    db.add(review)
    db.commit()
# Update averages
    update_venue_averages(db, venue.id)

    return RedirectResponse(url="/reviews", status_code=303)

# ---------------------------------------------------------
# EDIT EXISTING REVIEW
# ---------------------------------------------------------

@app.get("/reviews/{review_id}/edit")
def edit_review_page(
    request: Request,
    review_id: int,
    db: Session = Depends(get_db),
):
    r = db.query(models.Review).filter(models.Review.id == review_id).first()
    if not r:
        return RedirectResponse(url="/reviews?msg=notfound", status_code=303)

    form_data = {
        "venue_name": r.venue_name_raw or "",
        "location": r.venue_location_raw or "",
        "visit_date": str(r.visit_date) if r.visit_date else "",
        "reviewer_name": r.reviewer_name or "",
        "identity_pin": "",  # JS will populate from localStorage
        "coffee": r.coffee,
        "cost": r.cost,
        "service": r.service,
        "hygiene": r.hygiene,
        "ambience": r.ambience,
        "food": r.food,
        "notes": r.notes or "",
    }

    return templates.TemplateResponse(
        "new_review.html",
        {
            "request": request,
            "is_edit": True,
            "form_action": f"/reviews/{review_id}/edit",
            "existing_review_id": review_id,
            "form_data": form_data,
        },
    )

@app.post("/reviews/{review_id}/edit")
def edit_review_save(
    review_id: int,
    venue_name: str = Form(...),
    location: str = Form(...),
    visit_date: str = Form(...),
    reviewer_name: str = Form(...),
    identity_pin: str = Form(...),
    coffee: int = Form(...),
    cost: int = Form(...),
    service: int = Form(...),
    hygiene: int = Form(...),
    ambience: int = Form(...),
    food: int = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    r = db.query(models.Review).filter(models.Review.id == review_id).first()
    if not r:
        return RedirectResponse(url="/reviews?msg=notfound", status_code=303)

    # Only allow the device that created it to edit
    if (identity_pin or "").strip() != (r.identity_pin or "").strip():
        return RedirectResponse(url="/reviews?msg=denied", status_code=303)

    # Get or create venue
    venue = (
        db.query(models.Venue)
        .filter(
            func.lower(models.Venue.name) == venue_name.strip().lower(),
            func.lower(models.Venue.location) == location.strip().lower(),
        )
        .first()
    )

    if not venue:
        venue = models.Venue(name=venue_name.strip(), location=location.strip())
        db.add(venue)
        db.commit()
        db.refresh(venue)

    old_venue_id = r.venue_id

    # Update review
    r.venue_id = venue.id
    r.venue_name_raw = venue.name
    r.venue_location_raw = venue.location

    r.reviewer_name = reviewer_name.strip()
    r.visit_date = visit_date

    r.coffee = coffee
    r.cost = cost
    r.service = service
    r.hygiene = hygiene
    r.ambience = ambience
    r.food = food
    r.notes = notes.strip()

    r.total_score = coffee + cost + service + hygiene + ambience + food
    r.category_count = 6

    db.commit()

    # Update averages for new venue (and old one if changed)
    update_venue_averages(db, venue.id)
    if old_venue_id and old_venue_id != venue.id:
        update_venue_averages(db, old_venue_id)

    return RedirectResponse(url="/reviews?msg=updated", status_code=303)


# ---------------------------------------------------------
# DUPLICATE UPDATE
# ---------------------------------------------------------
@app.post("/reviews/duplicate-update")
def duplicate_update(
    existing_review_id: int = Form(...),
    venue_id: int = Form(...),
    visit_date: str = Form(...),
    reviewer_name: str = Form(...),
    identity_pin: str = Form(...),
    coffee: int = Form(...),
    cost: int = Form(...),
    service: int = Form(...),
    hygiene: int = Form(...),
    ambience: int = Form(...),
    food: int = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    existing = db.query(models.Review).filter(models.Review.id == existing_review_id).first()
    if not existing:
        return RedirectResponse(url="/reviews?msg=notfound", status_code=303)

    # Only allow update if identity matches
    if existing.identity_pin != identity_pin:
        return RedirectResponse(url="/reviews?msg=denied", status_code=303)

    existing.reviewer_name = reviewer_name.strip()
    existing.visit_date = visit_date
    existing.coffee = coffee
    existing.cost = cost
    existing.service = service
    existing.hygiene = hygiene
    existing.ambience = ambience
    existing.food = food
    existing.notes = notes.strip()

    db.commit()

    update_venue_averages(db, venue_id)

    return RedirectResponse(url="/reviews?msg=updated", status_code=303)


# ---------------------------------------------------------
# DUPLICATE CANCEL
# ---------------------------------------------------------
@app.post("/reviews/duplicate-cancel")
def duplicate_cancel():
    return RedirectResponse(url="/reviews/new", status_code=303)
