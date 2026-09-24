"""
IP-SHAKTI Sahayak
SerpApi Google Maps Service
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv


# =========================================================
# CONFIGURATION
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(
    PROJECT_ROOT / ".env"
)

SERPAPI_API_KEY = os.getenv(
    "SERPAPI_API_KEY"
)

SERPAPI_URL = (
    "https://serpapi.com/search"
)


# =========================================================
# CHECK CONFIGURATION
# =========================================================

def check_configuration():

    if not SERPAPI_API_KEY:

        print(
            "ERROR: SERPAPI_API_KEY is not configured."
        )

        return False

    return True


# =========================================================
# SEARCH GOOGLE MAPS
# =========================================================

def search_places(
    query,
    latitude=None,
    longitude=None,
    zoom=14
):

    if not check_configuration():

        return {
            "status": "error",
            "message":
                "SerpApi API key is not configured.",
            "results": []
        }

    if not query or not query.strip():

        return {
            "status": "error",
            "message":
                "Search query is required.",
            "results": []
        }

    params = {
        "engine": "google_maps",
        "q": query.strip(),
        "type": "search",
        "api_key": SERPAPI_API_KEY,
        "hl": "en"
    }

    if (
        latitude is not None
        and longitude is not None
    ):

        params["ll"] = (
            f"@{latitude},{longitude},{zoom}z"
        )

    try:

        response = requests.get(
            SERPAPI_URL,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if "error" in data:

            return {
                "status": "error",
                "message":
                    data["error"],
                "results": []
            }

        places = []

        for place in data.get(
            "local_results",
            []
        ):

            gps = place.get(
                "gps_coordinates",
                {}
            )

            places.append(
                {
                    "name":
                        place.get(
                            "title",
                            "Unknown"
                        ),

                    "address":
                        place.get(
                            "address",
                            ""
                        ),

                    "phone":
                        place.get(
                            "phone",
                            ""
                        ),

                    "rating":
                        place.get(
                            "rating"
                        ),

                    "reviews":
                        place.get(
                            "reviews"
                        ),

                    "type":
                        place.get(
                            "type",
                            ""
                        ),

                    "website":
                        place.get(
                            "website",
                            ""
                        ),

                    "latitude":
                        gps.get(
                            "latitude"
                        ),

                    "longitude":
                        gps.get(
                            "longitude"
                        ),

                    "place_id":
                        place.get(
                            "place_id"
                        ),

                    "maps_link":
                        (
                            place
                            .get(
                                "links",
                                {}
                            )
                            .get(
                                "directions",
                                ""
                            )
                        )
                }
            )

        return {
            "status": "success",
            "query": query,
            "count": len(places),
            "results": places
        }

    except requests.exceptions.Timeout:

        return {
            "status": "error",
            "message":
                "SerpApi request timed out.",
            "results": []
        }

    except requests.exceptions.RequestException as error:

        print(
            "SerpApi request failed:",
            repr(error)
        )

        return {
            "status": "error",
            "message":
                "Could not connect to SerpApi.",
            "results": []
        }

    except Exception as error:

        print(
            "Map service error:",
            repr(error)
        )

        return {
            "status": "error",
            "message":
                "Map search failed.",
            "results": []
        }
