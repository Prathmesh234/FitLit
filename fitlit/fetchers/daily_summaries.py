"""daily_summaries — once-an-hour daily roll-ups (every 3600s).

Resting HR, VO2 max, HRV, SpO2, respiratory rate, exercise sessions, etc.
"""
from fitlit.fetchers.base import main

NAME = "daily_summaries"

if __name__ == "__main__":
    main(NAME)
