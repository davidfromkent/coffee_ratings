from fastapi import FastAPI, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

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
    venue_name_clean = venue_name.strip()
    location_clean = location.strip()

    venue = (
        db.query(models.Venue)
        .filter(
            func.lower(models.Venue.name) == venue_name_clean.lower(),
            func.lower(models.Venue.location) == location_clean.lower(),
        )
        .first()
    )

    if venue:
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

    if food == 0:
        total_score = coffee + cost + service + hygiene + ambience
        category_count = 5
    else:
        total_score = coffee + cost + service + hygiene + ambience + food
        category_count = 6

    with db.begin():
        if not venue:
            venue = models.Venue(name=venue_name_clean, location=location_clean)
            db.add(venue)
            db.flush()

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

    update_venue_averages(db, venue.id)

    return RedirectResponse(url="/reviews", status_code=303)


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
    if not r:
        return RedirectResponse(url="/reviews?msg=notfound", status_code=303)

    if r.identity_pin != identity_pin:
        return RedirectResponse(url="/reviews?msg=denied", status_code=303)

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

    return RedirectResponse(url="/reviews?msg=updated", status_code=303)
