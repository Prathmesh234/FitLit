"""Pydantic models for Google Health API data points.

Every data point the API returns shares one envelope:

    {
      "name": "users/{user}/dataTypes/{dataType}/dataPoints/{id}",  # unique id
      "dataSource": {"recordingMethod": ..., "platform": ..., "device": {...}},
      "<dataType>": { ...the type-specific data object... }
    }

The ``<dataType>`` object is one of four *shapes*:

    Interval  -> {"interval": {"startTime","endTime","startUtcOffset",...}, ...}
    Sample    -> {"sampleTime": <ts or {"time","utcOffset"}>, ...}
    Daily     -> a day-level summary (often an interval spanning the day)
    Session   -> {"interval": {...}, nested arrays (sleepStages[], ...) }

Design for a *lifetime* of data and an *evolving* API:

* :class:`DataPoint` is the common envelope captured as real, queryable columns.
* Well-documented types get a subclass adding typed value columns (``count``,
  ``meters``, ``beats_per_minute``, ...) so the hot metrics are first-class.
* **Crucially**, every row also stores ``data_json`` (the full type-specific
  object) and ``raw_json`` (the entire untouched data point). So *no field is
  ever dropped*, even for types we don't model yet or fields Google adds later.

The storage layer (``fitlit.storage``) generates each table's DDL from these
models, so the models are the single source of truth for the schema.
"""
from __future__ import annotations

import hashlib
import json
import typing
from datetime import datetime
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Common sub-structures
# --------------------------------------------------------------------------- #
class DataSource(BaseModel):
    """Flattened view of a data point's ``dataSource`` block."""
    recording_method: Optional[str] = None
    platform: Optional[str] = None
    device_name: Optional[str] = None

    @classmethod
    def from_raw(cls, d: dict | None) -> "DataSource":
        d = d or {}
        device = d.get("device") or {}
        return cls(
            recording_method=d.get("recordingMethod"),
            platform=d.get("platform"),
            device_name=device.get("displayName") or device.get("formFactor"),
        )


def _extract_times(data: dict) -> dict[str, Optional[str]]:
    """Pull start/end timestamps + UTC offsets from any shape, defensively.

    Times are kept as the original RFC-3339 strings (lossless, and UTC 'Z'
    values sort correctly lexicographically for range queries). The full truth
    is always in ``raw_json`` regardless.
    """
    out: dict[str, Optional[str]] = {
        "start_time": None, "end_time": None,
        "start_utc_offset": None, "end_utc_offset": None,
    }
    interval = data.get("interval")
    if isinstance(interval, dict):
        out["start_time"] = interval.get("startTime")
        out["end_time"] = interval.get("endTime")
        out["start_utc_offset"] = interval.get("startUtcOffset")
        out["end_utc_offset"] = interval.get("endUtcOffset")
        return out

    sample = data.get("sampleTime", data.get("time"))
    if isinstance(sample, dict):
        out["start_time"] = out["end_time"] = sample.get("time")
        out["start_utc_offset"] = out["end_utc_offset"] = sample.get("utcOffset")
    elif isinstance(sample, str):
        out["start_time"] = out["end_time"] = sample

    # Daily types may carry a plain date.
    if out["start_time"] is None and isinstance(data.get("date"), str):
        out["start_time"] = out["end_time"] = data["date"]
    return out


# --------------------------------------------------------------------------- #
# Envelope (base for every data point)
# --------------------------------------------------------------------------- #
class DataPoint(BaseModel):
    """Common, queryable envelope shared by every Google Health data point."""

    # Keep any unexpected top-level keys rather than erroring — lifetime safety.
    model_config = ConfigDict(extra="allow")

    name: str                                   # globally-unique id -> natural key
    data_type: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    start_utc_offset: Optional[str] = None
    end_utc_offset: Optional[str] = None
    recording_method: Optional[str] = None
    platform: Optional[str] = None
    device_name: Optional[str] = None
    update_time: Optional[str] = None
    fetched_at: datetime                        # when our cron pulled it
    data_json: dict[str, Any] = Field(default_factory=dict)   # full type object
    raw_json: dict[str, Any] = Field(default_factory=dict)    # entire data point

    # Subclasses set these.
    DATA_TYPE: ClassVar[str] = ""               # "" == generic envelope only
    VALUE_FIELDS: ClassVar[tuple[str, ...]] = ()  # names of typed value columns

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        """Pull this type's typed scalar values from the data object.

        Base = none. Overridden by typed subclasses. Always tolerant: a missing
        or renamed field yields ``None``, never an error (the value still lives
        in ``data_json``).
        """
        return {}

    @staticmethod
    def _synthetic_name(data_type: str, point: dict, times: dict) -> str:
        """A *deterministic* natural key for points the API returns without a ``name``.

        Some Google Health data types (e.g. ``steps``) come back with no ``name``
        field. Earlier this fell back to ``id(point)`` — a Python memory address
        that changes every fetch — so re-fetching the same point created a brand-new
        row each cycle and the ``ON CONFLICT(name)`` upsert never deduped (one day
        of steps ballooned to ~1.9M rows for ~1.5k real points). Hashing the point's
        content instead makes identical points collapse to the same key across
        fetches, while genuinely different points at the same timestamp still get
        distinct keys. No field is lost — the full point still lives in ``raw_json``.
        """
        digest = hashlib.sha1(
            json.dumps(point, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"{data_type}/{times.get('start_time')}/{digest}"

    @classmethod
    def from_raw(cls, data_type: str, point: dict, fetched_at: datetime) -> "DataPoint":
        data = point.get(data_type)
        if not isinstance(data, dict):
            data = {}
        times = _extract_times(data)
        source = DataSource.from_raw(point.get("dataSource"))
        name = point.get("name") or cls._synthetic_name(data_type, point, times)
        return cls(
            name=name,
            data_type=data_type,
            recording_method=source.recording_method,
            platform=source.platform,
            device_name=source.device_name,
            update_time=data.get("updateTime") or data.get("modifyTime"),
            fetched_at=fetched_at,
            data_json=data,
            raw_json=point,
            **times,
            **cls.extract_values(data),
        )


# --------------------------------------------------------------------------- #
# Registry of typed models
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, type[DataPoint]] = {}


def register(model: type[DataPoint]) -> type[DataPoint]:
    REGISTRY[model.DATA_TYPE] = model
    return model


def model_for(data_type: str) -> type[DataPoint]:
    """Return the typed model for a data type, or the generic envelope."""
    return REGISTRY.get(data_type, DataPoint)


# --------------------------------------------------------------------------- #
# Typed models (confirmed value fields from the v4 reference)
# --------------------------------------------------------------------------- #
@register
class Steps(DataPoint):
    DATA_TYPE = "steps"
    VALUE_FIELDS = ("count",)
    count: Optional[int] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"count": data.get("count")}


@register
class Distance(DataPoint):
    DATA_TYPE = "distance"
    VALUE_FIELDS = ("meters",)
    meters: Optional[float] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"meters": data.get("meters")}


@register
class Floors(DataPoint):
    DATA_TYPE = "floors"
    VALUE_FIELDS = ("floors",)
    floors: Optional[float] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"floors": data.get("floors")}


@register
class Altitude(DataPoint):
    DATA_TYPE = "altitude"
    VALUE_FIELDS = ("gain_millimeters",)
    gain_millimeters: Optional[int] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"gain_millimeters": data.get("gainMillimeters")}


@register
class HeartRate(DataPoint):
    DATA_TYPE = "heartRate"
    VALUE_FIELDS = ("beats_per_minute",)
    beats_per_minute: Optional[int] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"beats_per_minute": data.get("beatsPerMinute")}


@register
class Weight(DataPoint):
    DATA_TYPE = "weight"
    VALUE_FIELDS = ("weight_kg",)
    weight_kg: Optional[float] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"weight_kg": data.get("weightKg")}


@register
class BloodGlucose(DataPoint):
    DATA_TYPE = "bloodGlucose"
    VALUE_FIELDS = ("blood_glucose_mg_per_dl",)
    blood_glucose_mg_per_dl: Optional[float] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"blood_glucose_mg_per_dl": data.get("bloodGlucoseMilligramsPerDeciliter")}


@register
class Sleep(DataPoint):
    DATA_TYPE = "sleep"
    VALUE_FIELDS = ("sleep_type",)
    sleep_type: Optional[str] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        return {"sleep_type": data.get("sleepType")}


@register
class Exercise(DataPoint):
    DATA_TYPE = "exercise"
    VALUE_FIELDS = (
        "exercise_type", "display_name", "active_duration",
        "calories_kcal", "distance_millimeters", "exercise_steps",
        "active_zone_minutes",
    )
    exercise_type: Optional[str] = None
    display_name: Optional[str] = None
    active_duration: Optional[str] = None
    calories_kcal: Optional[float] = None
    distance_millimeters: Optional[int] = None
    exercise_steps: Optional[int] = None
    active_zone_minutes: Optional[int] = None

    @classmethod
    def extract_values(cls, data: dict) -> dict[str, Any]:
        m = data.get("metricsSummary") or {}
        return {
            "exercise_type": data.get("exerciseType"),
            "display_name": data.get("displayName"),
            "active_duration": data.get("activeDuration"),
            "calories_kcal": m.get("caloriesKcal"),
            # the reference example spells it "distanceMillimiters"; read both.
            "distance_millimeters": m.get("distanceMillimeters", m.get("distanceMillimiters")),
            "exercise_steps": m.get("steps"),
            "active_zone_minutes": m.get("activeZoneMinutes"),
        }


# --------------------------------------------------------------------------- #
# DDL helper: map a model field's annotation to a SQLite column type
# --------------------------------------------------------------------------- #
def sqlite_type_for(model: type[DataPoint], field: str) -> str:
    """INTEGER / REAL / TEXT for a typed value field, by its annotation."""
    annotation = model.model_fields[field].annotation
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    base = args[0] if args else annotation
    if base is int:
        return "INTEGER"
    if base is float:
        return "REAL"
    return "TEXT"
