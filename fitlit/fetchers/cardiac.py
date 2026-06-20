"""cardiac — event-driven medical signals (every 300s).

Electrocardiogram (ECG measurement + rhythm classification) and
irregular-rhythm-notification (AFib) events. Each needs its own OAuth scope
(ecg.readonly / irn.readonly) — see fitlit/config.py.
"""
from fitlit.fetchers.base import main

NAME = "cardiac"

if __name__ == "__main__":
    main(NAME)
