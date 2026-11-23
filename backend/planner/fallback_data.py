"""Hand-picked places used when live OpenStreetMap data is unavailable.

Coordinates are approximate. Costs are per-person estimates in INR.
"""

from __future__ import annotations

# (name, category, lat, lon, extras)
CURATED: dict[str, list[tuple[str, str, float, float, dict]]] = {
    "bengaluru": [
        ("Cubbon Park", "park", 12.9763, 77.5929, {"opening_hours": "Mo-Su 06:00-18:00", "crowd": "medium"}),
        ("Lalbagh Botanical Garden", "garden", 12.9507, 77.5848, {"opening_hours": "Mo-Su 06:00-19:00", "est_cost": 30}),
        ("Ulsoor Lake", "lake", 12.9822, 77.6200, {"crowd": "low"}),
        ("Sankey Tank", "lake", 13.0094, 77.5736, {"crowd": "low"}),
        ("National Gallery of Modern Art", "gallery", 12.9886, 77.5880, {"opening_hours": "Tu-Su 10:00-17:00", "est_cost": 20}),
        ("Visvesvaraya Industrial & Technological Museum", "museum", 12.9752, 77.5963, {"opening_hours": "Mo-Su 09:30-18:00", "est_cost": 100}),
        ("Rangoli Metro Art Center", "arts_centre", 12.9755, 77.6069, {"crowd": "low"}),
        ("Bangalore International Centre", "arts_centre", 12.9610, 77.6385, {"note": "talks, concerts and screenings; check the schedule"}),
        ("Blossom Book House", "bookstore", 12.9738, 77.6066, {"opening_hours": "Mo-Su 10:30-21:00"}),
        ("Atta Galatta", "bookstore", 12.9339, 77.6238, {"note": "bookstore-café with occasional readings and gigs"}),
        ("Ranga Shankara", "theatre", 12.9106, 77.5859, {"est_cost": 400}),
        ("Bangalore Palace", "historic", 12.9988, 77.5921, {"opening_hours": "Mo-Su 10:00-17:30", "est_cost": 300, "crowd": "high"}),
        ("Vidyarthi Bhavan", "restaurant", 12.9452, 77.5713, {"veg": "only", "est_cost": 150, "crowd": "high", "cuisine": "south_indian"}),
        ("MTR (Mavalli Tiffin Rooms)", "restaurant", 12.9552, 77.5856, {"veg": "only", "est_cost": 300, "crowd": "high", "cuisine": "south_indian"}),
        ("Brahmin's Coffee Bar", "cafe", 12.9536, 77.5698, {"veg": "only", "est_cost": 80, "cuisine": "south_indian"}),
        ("Green Theory", "restaurant", 12.9686, 77.6040, {"veg": "only", "est_cost": 800, "crowd": "low"}),
        ("Koshy's", "restaurant", 12.9747, 77.6006, {"veg": "yes", "est_cost": 500}),
        ("Third Wave Coffee, Indiranagar", "cafe", 12.9784, 77.6400, {"veg": "yes", "est_cost": 350, "crowd": "low"}),
        ("Corner House Ice Creams", "dessert", 12.9705, 77.6017, {"veg": "yes", "est_cost": 200}),
    ],
    "mumbai": [
        ("Marine Drive Promenade", "viewpoint", 18.9432, 72.8235, {"crowd": "high"}),
        ("Hanging Gardens", "garden", 18.9568, 72.8051, {"opening_hours": "Mo-Su 05:00-21:00"}),
        ("Carter Road Promenade", "viewpoint", 19.0657, 72.8226, {"crowd": "medium"}),
        ("Bandra Fort", "historic", 19.0418, 72.8185, {"crowd": "medium"}),
        ("Chhatrapati Shivaji Maharaj Vastu Sangrahalaya", "museum", 18.9269, 72.8326, {"opening_hours": "Mo-Su 10:15-18:00", "est_cost": 150}),
        ("Jehangir Art Gallery", "gallery", 18.9275, 72.8317, {"opening_hours": "Mo-Su 11:00-19:00", "crowd": "low"}),
        ("National Centre for the Performing Arts", "theatre", 18.9256, 72.8197, {"est_cost": 500}),
        ("Prithvi Theatre", "theatre", 19.1062, 72.8264, {"est_cost": 400}),
        ("Kitab Khana", "bookstore", 18.9322, 72.8339, {"opening_hours": "Mo-Su 10:30-20:00", "crowd": "low"}),
        ("Gateway of India", "historic", 18.9220, 72.8347, {"crowd": "high"}),
        ("Swati Snacks", "restaurant", 18.9697, 72.8150, {"veg": "only", "est_cost": 450, "crowd": "high", "cuisine": "gujarati"}),
        ("Shree Thaker Bhojanalay", "restaurant", 18.9496, 72.8287, {"veg": "only", "est_cost": 650, "cuisine": "gujarati_thali"}),
        ("Cafe Madras", "restaurant", 19.0274, 72.8557, {"veg": "only", "est_cost": 250, "crowd": "high", "cuisine": "south_indian"}),
        ("Kala Ghoda Cafe", "cafe", 18.9289, 72.8317, {"veg": "yes", "est_cost": 500}),
        ("Prithvi Cafe", "cafe", 19.1062, 72.8264, {"veg": "yes", "est_cost": 350}),
        ("K. Rustom & Co.", "dessert", 18.9322, 72.8241, {"veg": "yes", "est_cost": 120}),
    ],
    "delhi": [
        ("Lodhi Garden", "garden", 28.5931, 77.2197, {"opening_hours": "Mo-Su 06:00-20:00", "crowd": "medium"}),
        ("Sunder Nursery", "park", 28.5933, 77.2440, {"opening_hours": "Mo-Su 07:00-22:00", "est_cost": 35, "crowd": "low"}),
        ("Humayun's Tomb", "historic", 28.5933, 77.2507, {"opening_hours": "Mo-Su 06:00-18:00", "est_cost": 40, "crowd": "high"}),
        ("National Museum", "museum", 28.6118, 77.2193, {"opening_hours": "Tu-Su 10:00-18:00", "est_cost": 20}),
        ("Kiran Nadar Museum of Art", "gallery", 28.5286, 77.2190, {"opening_hours": "Tu-Su 10:30-18:30", "crowd": "low"}),
        ("India Habitat Centre", "arts_centre", 28.5891, 77.2253, {"note": "free exhibitions, frequent concerts"}),
        ("Hauz Khas Lake & Deer Park", "lake", 28.5545, 77.1940, {"crowd": "medium"}),
        ("Bahrisons Booksellers", "bookstore", 28.6003, 77.2267, {"opening_hours": "Mo-Su 10:30-19:30", "crowd": "low"}),
        ("Kamani Auditorium", "theatre", 28.6253, 77.2337, {"est_cost": 400}),
        ("Dilli Haat INA", "market", 28.5733, 77.2077, {"est_cost": 30, "crowd": "high"}),
        ("Saravana Bhavan, Connaught Place", "restaurant", 28.6328, 77.2197, {"veg": "only", "est_cost": 350, "crowd": "high", "cuisine": "south_indian"}),
        ("Sagar Ratna, Defence Colony", "restaurant", 28.5735, 77.2307, {"veg": "only", "est_cost": 400, "cuisine": "south_indian"}),
        ("The Big Chill Cafe, Khan Market", "cafe", 28.6004, 77.2273, {"veg": "yes", "est_cost": 900, "crowd": "high"}),
        ("Triveni Terrace Cafe", "cafe", 28.6230, 77.2349, {"veg": "yes", "est_cost": 250, "crowd": "low"}),
        ("Kuremal Mohan Lal Kulfi", "dessert", 28.6496, 77.2272, {"veg": "only", "est_cost": 150}),
    ],
}

CITY_KEYS = {
    "bengaluru": "bengaluru", "bangalore": "bengaluru",
    "mumbai": "mumbai", "bombay": "mumbai",
    "delhi": "delhi", "new delhi": "delhi",
}


def curated_for(city: str) -> list[tuple[str, str, float, float, dict]] | None:
    key = CITY_KEYS.get(city.strip().lower())
    return CURATED.get(key) if key else None
