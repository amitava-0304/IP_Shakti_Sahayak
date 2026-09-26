import sys
import requests

URL = "http://127.0.0.1:8000/health"

try:
    response = requests.get(URL, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "ok":
        print("Health check failed:", data)
        sys.exit(1)

    print("Health check passed:", data)

except Exception as error:
    print("Health check failed:", error)
    sys.exit(1)
