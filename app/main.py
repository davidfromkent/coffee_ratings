from fastapi import FastAPI, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy.orm import Session
from typing import Optional

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
        return

    def safe_avg(values):
        return sum(values) / len(values) if values else None

    avg_coffee = safe_avg([r.coffee for r in reviews])
    avg_cost = safe_avg([r.cost for r in reviews])
    avg_service = safe_avg([r.service for r in reviews])
    avg_hygiene = safe_avg([r.hygiene for r in reviews])
    avg_ambience = safe_avg([r.ambience for r in reviews])
    avg_food = safe_avg([r.food for r in reviews if r.food != 0])

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
# ROUTES
# ---------------------------------------------------------


# HOME PAGE
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


# VENUES LIST
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


# SINGLE VENUE PAGE (marker-based)
@app.get("/venues/{venue_id}", response_class=HTMLResponse)
def venue_detail(
    venue_id: int,
    request: Request,
    from_param: Optional[str] = None,
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
        },
    )


# ALL REVIEWS (with search)
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

    # Sorting
    if sort == "high":
        query = query.order_by((models.Venue.avg_total_score).desc())
    elif sort == "low":
        query = query.order_by((models.Venue.avg_total_score).asc())

    reviews = query.all()

    return templates.TemplateResponse(
        "reviews.html",
        {
            "request": request,
            "reviews": reviews,
            "title": "Reviews",
            "back_url": "/",
            "search_query": search_query,
            "sort": sort,
            "msg": msg,
        },
    )


# ADD REVIEW FORM (marker preserved)
@app.get("/reviews/new", response_class=HTMLResponse)
def new_review_form(
    request: Request,
    venue_id: Optional[int] = None,
    from_param: Optional[str] = None,
    db: Session = Depends(get_db),
):
    venue = None
    if venue_id is not None:
        venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()

    # Decide back target
    if venue_id is not None:
        # Return to the same venue, carrying the from marker forward
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


# Duplicate check endpoint (used by frontend)
@app.get("/check-duplicate")
def check_duplicate(
    venue_name: str,
    location: str,
    visit_date: str,
    identity_pin: str,
    db: Session = Depends(get_db),
):
    venue = db.query(models.Venue).filter(
        models.Venue.name.ilike(venue_name),
        models.Venue.location.ilike(location)
    ).first()

    if not venue:
        return {"duplicate": False}

    exists = db.query(models.Review).filter(
        models.Review.venue_id == venue.id,
        models.Review.identity_pin == identity_pin,
        models.Review.visit_date == visit_date
    ).first()

    return {"duplicate": exists is not None}


# SUBMIT REVIEW
@app.post("/reviews/new")
def create_review(
    request: Request,
    reviewer_name: str = Form(...),
    identity_pin: str = Form(...),
    venue_name: str = Form(...),
    location: str = Form(...),
    coffee: int = Form(...),
    cost: int = Form(...),
    service: int = Form(...),
    hygiene: int = Form(...),
    ambience: int = Form(...),
    food: int = Form(...),
    notes: str = Form(""),
    visit_date: str = Form(...),
    db: Session = Depends(get_db),
):
    # Fallback to prevent NOT NULL errors
    if not identity_pin or identity_pin.strip() == "":
        identity_pin = "000000"

    # Find or create venue
    existing = db.query(models.Venue).filter(
        models.Venue.name.ilike(venue_name),
        models.Venue.location.ilike(location),
    ).first()

    if existing:
        venue = existing
    else:
        venue = models.Venue(name=venue_name, location=location)
        db.add(venue)
        db.commit()
        db.refresh(venue)

    # Calculate score components
    scores = [coffee, cost, service, hygiene, ambience]
    if food != 0:
        scores.append(food)

    total_score = sum(scores)
    category_count = len(scores)

    # Duplicate review check (RULES)
    # One review per user per day per venue, user is identity_pin, venue is venue.id, day is visit_date
    existing_review = db.query(models.Review).filter(
        models.Review.identity_pin == identity_pin,
        models.Review.venue_id == venue.id,
        models.Review.visit_date == visit_date,
    ).first()

    if existing_review:
        # Show a prompt offering Update or Cancel
        return templates.TemplateResponse(
            "duplicate_prompt.html",
            {
                "request": request,
                "message_primary": "You can only leave one review per venue per day.",
                "message_secondary": "You’ve already reviewed this place on that date. Would you like to update your existing review instead?",
                "existing_review_id": existing_review.id,
                "venue_id": venue.id,
                "venue_name": venue_name,
                "location": location,
                "reviewer_name": reviewer_name,
                "identity_pin": identity_pin,
                "visit_date": visit_date,
                "coffee": coffee,
                "cost": cost,
                "service": service,
                "hygiene": hygiene,
                "ambience": ambience,
                "food": food,
                "notes": notes,
            }
        )

    # Create new review
    review = models.Review(
        venue_id=venue.id,
        venue_name_raw=venue_name,
        venue_location_raw=location,
        reviewer_name=reviewer_name,
        identity_pin=identity_pin,
        coffee=coffee,
        cost=cost,
        service=service,
        hygiene=hygiene,
        ambience=ambience,
        food=food,
        total_score=total_score,
        category_count=category_count,
        notes=notes,
        visit_date=visit_date,
    )

    db.add(review)
    db.commit()

    update_venue_averages(db, venue.id)

    return RedirectResponse(url="/reviews?msg=updated", status_code=303)


# CONFIRM UPDATE EXISTING DUPLICATE
@app.post("/reviews/duplicate-update")
def duplicate_update(
    request: Request,
    existing_review_id: int = Form(...),
    reviewer_name: str = Form(...),
    identity_pin: str = Form(...),
    venue_id: int = Form(...),
    venue_name: str = Form(...),
    location: str = Form(...),
    coffee: int = Form(...),
    cost: int = Form(...),
    service: int = Form(...),
    hygiene: int = Form(...),
    ambience: int = Form(...),
    food: int = Form(...),
    notes: str = Form(""),
    visit_date: str = Form(...),
    db: Session = Depends(get_db),
):
    # Safety fallback
    if not identity_pin or identity_pin.strip() == "":
        identity_pin = "000000"

    review = db.query(models.Review).filter(models.Review.id == existing_review_id).first()
    if not review:
        return RedirectResponse(url="/reviews/new", status_code=303)

    # Guard: ensure the review being updated really belongs to this user and venue and date
    if review.identity_pin != identity_pin or review.venue_id != venue_id or review.visit_date != visit_date:
        return RedirectResponse(url="/reviews/new", status_code=303)

    scores = [coffee, cost, service, hygiene, ambience]
    if food != 0:
        scores.append(food)

    total_score = sum(scores)
    category_count = len(scores)

    # Update the existing record in place
    review.reviewer_name = reviewer_name
    review.venue_name_raw = venue_name
    review.venue_location_raw = location
    review.coffee = coffee
    review.cost = cost
    review.service = service
    review.hygiene = hygiene
    review.ambience = ambience
    review.food = food
    review.total_score = total_score
    review.category_count = category_count
    review.notes = notes
    review.visit_date = visit_date

    db.commit()

    update_venue_averages(db, venue_id)

    return RedirectResponse(url="/reviews?msg=updated", status_code=303)



# CANCEL DUPLICATE (DO NOTHING)
@app.post("/reviews/duplicate-cancel")
def duplicate_cancel(
    request: Request,
):
    return RedirectResponse(url="/reviews/new?msg=cancelled", status_code=303)
