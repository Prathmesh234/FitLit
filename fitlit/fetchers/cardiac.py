"""cardiac — event-driven medical signals (every 300s).

ECG measurements + rhythm classification, AFib analysis windows.
"""
from fitlit.fetchers.base import main

NAME = "cardiac"

if __name__ == "__main__":
    main(NAME)
