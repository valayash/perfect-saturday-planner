"""Hand-picked, well-known places with hours, prices and veg status. Merged in ahead of live OpenStreetMap
results, and the fallback when live data is unavailable.

Coordinates are approximate. Costs are per-person estimates in INR.
"""

from __future__ import annotations

from .geo import city_key

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
        ("Bangalore International Centre", "arts_centre", 12.9610, 77.6385, {"opening_hours": "Mo-Su 10:00-21:30", "note": "talks, concerts and screenings; check the schedule"}),
        ("Blossom Book House", "bookstore", 12.9738, 77.6066, {"opening_hours": "Mo-Su 10:30-21:00"}),
        ("Atta Galatta", "bookstore", 12.9339, 77.6238, {"note": "bookstore-café with occasional readings and gigs"}),
        ("Ranga Shankara", "theatre", 12.9106, 77.5859, {"est_cost": 400}),
        ("Bangalore Palace", "historic", 12.9988, 77.5921, {"opening_hours": "Mo-Su 10:00-17:30", "est_cost": 300, "crowd": "high"}),
        ("Vidyarthi Bhavan", "restaurant", 12.9452, 77.5713, {"veg": "only", "est_cost": 150, "crowd": "high", "cuisine": "south_indian"}),
        ("MTR (Mavalli Tiffin Rooms)", "restaurant", 12.9552, 77.5856, {"veg": "only", "est_cost": 300, "crowd": "high", "cuisine": "south_indian"}),
        ("Brahmin's Coffee Bar", "cafe", 12.9536, 77.5698, {"veg": "only", "est_cost": 80, "cuisine": "south_indian"}),
        ("Green Theory", "restaurant", 12.9686, 77.6040, {"opening_hours": "Mo-Su 12:00-23:00", "veg": "only", "est_cost": 800, "crowd": "low"}),
        ("Koshy's", "restaurant", 12.9747, 77.6006, {"opening_hours": "Mo-Su 09:00-23:00", "veg": "yes", "est_cost": 500}),
        ("Third Wave Coffee, Indiranagar", "cafe", 12.9784, 77.6400, {"opening_hours": "Mo-Su 08:00-23:00", "veg": "yes", "est_cost": 350, "crowd": "low"}),
        ("Burma Burma, Indiranagar", "restaurant", 12.9716, 77.6411, {"veg": "only", "est_cost": 850, "crowd": "medium", "cuisine": "burmese"}),
        ("Rameshwaram Cafe, Indiranagar", "restaurant", 12.9784, 77.6385, {"veg": "only", "est_cost": 200, "crowd": "high", "cuisine": "south_indian", "note": "famous for long queues"}),
        ("Carrots, Koramangala", "restaurant", 12.9352, 77.6196, {"veg": "only", "est_cost": 500, "crowd": "low", "cuisine": "vegan"}),
        ("Sattvam, Sadashivanagar", "restaurant", 13.0067, 77.5800, {"veg": "only", "est_cost": 700, "crowd": "low", "cuisine": "sattvic"}),
        ("Dyu Art Cafe, Koramangala", "cafe", 12.9346, 77.6280, {"veg": "yes", "est_cost": 400, "crowd": "low", "note": "art-filled café in an old house"}),
        ("Matteo Coffea, Church Street", "cafe", 12.9746, 77.6050, {"opening_hours": "Mo-Su 08:00-23:00", "veg": "yes", "est_cost": 350}),
        ("Goethe-Institut (Max Mueller Bhavan)", "arts_centre", 12.9786, 77.6389, {"crowd": "low", "note": "talks, screenings and exhibitions; check the events page"}),
        ("Alliance Française de Bangalore", "arts_centre", 12.9915, 77.5915, {"crowd": "low", "note": "concerts, films and exhibitions"}),
        ("Chowdiah Memorial Hall", "live_music", 13.0014, 77.5710, {"est_cost": 500, "crowd": "medium", "note": "concert hall; check what's on"}),
        ("Indian Music Experience Museum", "museum", 12.8946, 77.5843, {"opening_hours": "Tu-Su 10:00-18:00", "est_cost": 300, "crowd": "low", "note": "interactive music museum"}),
        ("Venkatappa Art Gallery", "gallery", 12.9750, 77.5960, {"opening_hours": "Tu-Su 10:00-17:00", "crowd": "low"}),
        ("Bookworm, Church Street", "bookstore", 12.9750, 77.6051, {"crowd": "medium", "note": "packed second-hand bookshop"}),
        ("Agara Lake", "lake", 12.9254, 77.6429, {"crowd": "low"}),
        ("Corner House Ice Creams", "dessert", 12.9705, 77.6017, {"opening_hours": "Mo-Su 11:00-23:30", "veg": "yes", "est_cost": 200}),
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
        ("Candies, Bandra", "cafe", 19.0544, 72.8300, {"veg": "yes", "est_cost": 350, "crowd": "medium"}),
        ("Pali Village Cafe", "cafe", 19.0661, 72.8302, {"veg": "yes", "est_cost": 900, "crowd": "medium"}),
        ("Chapel Road street art", "gallery", 19.0566, 72.8320, {"crowd": "low", "note": "free open-air murals in Ranwar village lanes"}),
        ("Soam, Babulnath", "restaurant", 18.9586, 72.8114, {"veg": "only", "est_cost": 550, "cuisine": "gujarati"}),
        ("Bandstand Promenade", "viewpoint", 19.0456, 72.8190, {"crowd": "medium"}),
        ("Mount Mary Basilica", "historic", 19.0466, 72.8221, {"crowd": "medium"}),
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
        ("The Piano Man Jazz Club", "live_music", 28.5641, 77.1953, {"est_cost": 1000, "crowd": "medium", "note": "live jazz most evenings"}),
        ("Kunzum Travel Cafe", "cafe", 28.5537, 77.1940, {"veg": "yes", "est_cost": 150, "crowd": "low", "note": "pay-what-you-like café"}),
        ("Perch Wine & Coffee Bar, Khan Market", "cafe", 28.6002, 77.2269, {"veg": "yes", "est_cost": 900}),
        ("Full Circle Bookstore, Khan Market", "bookstore", 28.6005, 77.2270, {"crowd": "low"}),
        ("Lodhi Art District", "gallery", 28.5850, 77.2230, {"crowd": "low", "note": "open-air street art across Lodhi Colony"}),
        ("Agrasen ki Baoli", "historic", 28.6262, 77.2251, {"opening_hours": "Mo-Su 07:00-18:00", "crowd": "medium"}),
    ],
    "gurugram": [
        ("CyberHub, DLF Cyber City", "restaurant", 28.4950, 77.0890, {"veg": "yes", "est_cost": 800, "crowd": "high", "note": "dining hub with dozens of restaurants, cafés and bars"}),
        ("Sector 29 market", "bar", 28.4687, 77.0650, {"est_cost": 900, "crowd": "high", "note": "Gurugram's pub street, next to Leisure Valley"}),
        ("Galleria Market, DLF Phase IV", "market", 28.4669, 77.0827, {"crowd": "high", "note": "busy food and shopping strip"}),
        ("Ambience Mall, Gurugram", "mall", 28.5049, 77.0960, {"crowd": "high", "note": "large mall with a food court and an entertainment zone"}),
        ("Leisure Valley Park", "park", 28.4706, 77.0700, {"crowd": "medium"}),
    ],
}


def curated_for(city: str) -> list[tuple[str, str, float, float, dict]] | None:
    return CURATED.get(city_key(city) or "")
