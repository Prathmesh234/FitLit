"""live_activity — near-real-time movement streams (every 60s).

steps, distance, calories, active minutes / zone minutes, floors, etc.
"""
from fitlit.fetchers.base import main

NAME = "live_activity"

if __name__ == "__main__":
    main(NAME)
