from fastapi import FastAPI, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import date
import math

from .dependencies import get_db
from . import models
from .database import engine

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ---------------------------------------------------------
# Helper: Update all average scores for a venue
# ---------------------------------------------------------
def update_venue_averages(db, venue_id: int):
    reviews = db.query(models.Review).filter(models.Review.venue_id == venue_id).all()
    venue = db.query(models.Venue).filter(models.Venue.id == venue_id).first()
    if not venue:
        return

    if not reviews:
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
        return (sum(values) / len(values)) if values else None

    venue.avg_coffee = safe_avg([r.coffee for r in reviews])
    venue.avg_cost = safe_avg([r.cost for r in reviews])
    venue.avg_service = safe_avg([r.service for r in reviews])
    venue.avg_hygiene = safe_avg([r.hygiene for r in reviews])
    venue.avg_ambience = safe_avg([r.ambience for r in reviews])
    venue.avg_food = safe_avg([r.food for r in reviews if r.food != 0])

    total_points = sum(r.total_score or 0 for r in reviews)
    total_categories = sum(r.category_count or 0 for r in reviews)
    venue.avg_total_score = (total_points / total_categories) if total_categories else None

    db.commit()


# ---------------------------------------------------------
# Helper: add or replace ?msg=... on a URL
# ---------------------------------------------------------
def _add_msg(url: str, msg: str) -> str:
    parts = urlparse(url or "/reviews")
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["msg"] = msg
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(q), parts.fragment))


# ---------------------------------------------------------
# Helper: haversine distance in miles
# ---------------------------------------------------------
def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0088
    to_rad = math.radians
    dlat = to_rad(lat2 - lat1)
    dlon = to_rad(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    km = r_km * c
    miles = km * 0.621371
    return miles


# ---------------------------------------------------------
# DELETE REVIEW
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
    return templates.TemplateResponse("home.html", {"request": request, "title": "Coffee Ratings"})


# ---------------------------------------------------------
# VENUES LIST (with Near me support)
# ---------------------------------------------------------
@app.get("/venues", response_class=HTMLResponse)
def list_venues(
    request: Request,
    near_me: int = 0,
    radius: int = 5,
    sort: str = "distance",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    db: Session = Depends(get_db),
):
    venues = db.query(models.Venue).all()

    near_me_enabled = bool(near_me)

    # Default sort behaviour:
    # - if near_me enabled, default to distance
    # - otherwise default to rating (keeps it useful)
    if not sort:
        sort = "distance" if near_me_enabled else "rating"

    user_lat = lat
    user_lng = lng

    # Attach distance_miles where possible
    for v in venues:
        setattr(v, "distance_miles", None)
        if near_me_enabled and user_lat is not None and user_lng is not None:
            if v.latitude is not None and v.longitude is not None:
                try:
                    v.distance_miles = _haversine_miles(
                        float(user_lat), float(user_lng), float(v.latitude), float(v.longitude)
                    )
                except Exception:
                    v.distance_miles = None

    # If near_me enabled and we have user coords, filter to venues with coords and within radius
    if near_me_enabled and user_lat is not None and user_lng is not None:
        filtered = []
        for v in venues:
            if v.distance_miles is None:
                continue
            if v.distance_miles <= float(radius):
                filtered.append(v)
        venues = filtered

    # Sorting
    if near_me_enabled and sort == "distance":
        venues = sorted(
            venues,
            key=lambda v: (v.distance_miles is None, v.distance_miles if v.distance_miles is not None else 1e9),
        )
    elif sort == "value":
        venues = sorted(
            venues,
            key=lambda v: (v.avg_cost is None, -(v.avg_cost or 0), -(v.avg_total_score or 0), v.name.lower()),
        )
    else:
        # rating
        venues = sorted(
            venues,
            key=lambda v: (v.avg_total_score is None, -(v.avg_total_score or 0), v.name.lower()),
        )

    return templates.TemplateResponse(
        "venues.html",
        {
            "request": request,
            "venues": venues,
            "title": "Venues",
            "back_url": "/",
            "near_me": near_me_enabled,
            "radius": radius,
            "sort": sort,
            "user_lat": user_lat,
            "user_lng": user_lng,
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

    back_url = "/venues"
    if from_param == "reviews":
        back_url = "/reviews"

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
            query = query.filter(models.Venue.name.ilike(f"%{search_query}%"))

    if sort == "high":
        query = query.order_by(models.Review.total_score.desc())
    elif sort == "low":
        query = query.order_by(models.Review.total_score.asc())
    else:
        query = query.order_by(models.Review.visit_date.desc())

    reviews = query.all()

    return templates.TemplateResponse(
        "reviews.html",
        {
            "request": request,
            "reviews": reviews,
            "title": "Reviews",
            "search_query": search_query,
            "sort": sort,
            "msg": msg,
            "back_url": "/",
        },
    )


# ---------------------------------------------------------
# ADD REVIEW (FORM)
# ---------------------------------------------------------
@app.get("/reviews/new", response_class=HTMLResponse)
def new_review_form(request: Request):
    return templates.TemplateResponse(
        "new_review.html",
        {"request": request, "title": "Add Review", "back_url": "/"},
    )


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
    visit = date.fromisoformat(visit_date)
    if visit > date.today():
        return RedirectResponse("/reviews/new?msg=futuredate", status_code=303)

    venue = (
        db.query(models.Venue)
        .filter(
            func.lower(models.Venue.name) == venue_name.strip().lower(),
            func.lower(models.Venue.location) == location.strip().lower(),
        )
        .first()
    )

    if venue:
        dup = (
            db.query(models.Review)
            .filter(
                models.Review.identity_pin == identity_pin,
                models.Review.venue_id == venue.id,
                models.Review.visit_date == visit_date,
            )
            .first()
        )
        if dup:
            return templates.TemplateResponse(
                "duplicate_prompt.html",
                {
                    "request": request,
                    "existing_review": dup,
                    "venue_id": venue.id,
                    "visit_date": visit_date,
                },
            )

    if food == 0:
        total_score = coffee + cost + service + hygiene + ambience
        category_count = 5
    else:
        total_score = coffee + cost + service + hygiene + ambience + food
        category_count = 6

    with db.begin():
        if not venue:
            venue = models.Venue(name=venue_name.strip(), location=location.strip())
            db.add(venue)
            db.flush()

        db.add(
            models.Review(
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
        )

    update_venue_averages(db, venue.id)
    return RedirectResponse("/reviews", status_code=303)


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
    r = db.query(models.Review).filter(models.Review.id == existing_review_id).first()
    if not r or r.identity_pin != identity_pin:
        return RedirectResponse("/reviews?msg=denied", status_code=303)

    if food == 0:
        r.total_score = coffee + cost + service + hygiene + ambience
        r.category_count = 5
    else:
        r.total_score = coffee + cost + service + hygiene + ambience + food
        r.category_count = 6

    r.reviewer_name = reviewer_name.strip()
    r.visit_date = visit_date
    r.coffee = coffee
    r.cost = cost
    r.service = service
    r.hygiene = hygiene
    r.ambience = ambience
    r.food = food
    r.notes = notes.strip()

    db.commit()
    update_venue_averages(db, venue_id)

    return RedirectResponse("/reviews?msg=updated", status_code=303)


# ---------------------------------------------------------
# DUPLICATE CANCEL
# ---------------------------------------------------------
@app.post("/reviews/duplicate-cancel")
def duplicate_cancel():
    return RedirectResponse("/reviews/new", status_code=303)
