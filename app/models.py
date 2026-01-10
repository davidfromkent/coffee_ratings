from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=False)

    # NEW: used to distinguish branches
    postcode = Column(String, nullable=True)

    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String, nullable=True)

    # Averages per category
    avg_coffee = Column(Float, nullable=True)
    avg_cost = Column(Float, nullable=True)
    avg_service = Column(Float, nullable=True)
    avg_hygiene = Column(Float, nullable=True)
    avg_ambience = Column(Float, nullable=True)
    avg_food = Column(Float, nullable=True)
    avg_total_score = Column(Float, nullable=True)

    reviews = relationship("Review", back_populates="venue")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)

    # Raw text from the form (for history)
    venue_name_raw = Column(String, nullable=False)
    venue_location_raw = Column(String, nullable=False)

    # Scores
    coffee = Column(Integer, nullable=False)
    cost = Column(Integer, nullable=False)
    service = Column(Integer, nullable=False)
    hygiene = Column(Integer, nullable=False)
    ambience = Column(Integer, nullable=False)
    food = Column(Integer, nullable=False)

    total_score = Column(Integer, nullable=False)
    category_count = Column(Integer, nullable=False)

    # Extra fields
    notes = Column(Text, nullable=True)
    photo_path = Column(String, nullable=True)
    reviewer_name = Column(String, nullable=False)
    identity_pin = Column(String, nullable=False)

    visit_date = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    venue = relationship("Venue", back_populates="reviews")
