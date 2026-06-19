"""body — occasional body measurements (every 1800s).

Weight, body fat, height, body/core temperature, blood glucose.
"""
from fitlit.fetchers.base import main

NAME = "body"

if __name__ == "__main__":
    main(NAME)
