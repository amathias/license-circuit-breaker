"""The demo serving layer: prediction, retrieval, and export access.

This is the surface a judge probes before and after containment. Before the
rights event it genuinely answers with content derived from the licensed partner
feed. After an approved freeze it refuses, after a purge the index has nothing
partner-derived left to return, and after a quarantine the published export path
is simply gone.

Refusal is modelled as a *response*, not a crash. A frozen service still runs and
still answers -- with :class:`ServingRefused` and, over HTTP, ``451 Unavailable
For Legal Reasons``. A stopped process would be indistinguishable from an outage
and would prove nothing about containment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from demo import graph
from demo.estate import (
    EstateError,
    EstatePaths,
    ServingControl,
    active_version,
    export_path,
    load_index,
    load_scorer,
    training_manifest,
)
from demo.tfidf import TfidfModel, cosine

#: The model name the prediction endpoint serves.
SERVED_MODEL = "review_sentiment"


class ServingRefused(Exception):
    """Raised when a governed artifact declines to serve.

    Carries the URN and the reason so the probe can report *why* it was refused
    rather than only that something went wrong.
    """

    def __init__(self, urn: str, reason: str) -> None:
        super().__init__(reason)
        self.urn = urn
        self.reason = reason


@dataclass(frozen=True)
class Prediction:
    """One classifier response, with the provenance that justifies trusting it."""

    text: str
    label: int
    confidence: float
    model_version: str
    training_sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "label": self.label,
            "sentiment": "positive" if self.label == 1 else "negative",
            "confidence": round(self.confidence, 4),
            "model_version": self.model_version,
            "training_sources": list(self.training_sources),
        }


@dataclass(frozen=True)
class SearchHit:
    """One retrieval result, carrying the identifier that reveals its provenance."""

    review_id: str
    text: str
    score: float
    source_feed: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "text": self.text,
            "score": round(self.score, 4),
            "source_feed": self.source_feed,
        }


def _require_serving(paths: EstatePaths, urn: str) -> None:
    control = ServingControl.load(paths.serving_path)
    if control.is_blocked(urn):
        raise ServingRefused(
            urn,
            control.states.get(urn, {}).get("reason")
            or "serving is blocked by an approved containment action",
        )


def predict(paths: EstatePaths, text: str) -> Prediction:
    """Classify one review.

    Raises:
        ServingRefused: when the prediction API has been frozen.
        EstateError: when no active model is available to serve.
    """
    _require_serving(paths, graph.PREDICT_API)

    bundle = load_scorer(paths, SERVED_MODEL)
    version = active_version(paths, SERVED_MODEL)
    if bundle is None or version is None:
        raise EstateError("no active model is available; build the estate first")

    vectorizer = TfidfModel.from_dict(bundle["vectorizer"])
    row = vectorizer.dense(text)
    coefficients = bundle["coefficients"]
    logit = sum(w * x for w, x in zip(coefficients, row, strict=True)) + bundle["intercept"]
    probability = 1.0 / (1.0 + math.exp(-logit))

    manifest = training_manifest(paths, SERVED_MODEL)
    return Prediction(
        text=text,
        label=1 if probability >= 0.5 else 0,
        confidence=probability if probability >= 0.5 else 1.0 - probability,
        model_version=version,
        training_sources=tuple(manifest.get("training_sources", ())),
    )


def search(paths: EstatePaths, query: str, limit: int = 3) -> list[SearchHit]:
    """Retrieve the most similar indexed reviews.

    Returns an empty list when the index has been purged -- an empty result is
    the correct answer for an index with nothing in it, and is distinguishable
    from a refusal because no exception is raised.

    Raises:
        ServingRefused: when the search API has been frozen.
    """
    _require_serving(paths, graph.PREDICT_API)

    model, entries = load_index(paths)
    if model is None or not entries:
        return []

    query_vector = model.transform(query)
    scored = [
        SearchHit(
            review_id=str(entry["review_id"]),
            text=str(entry["text"]),
            score=cosine(query_vector, {k: float(v) for k, v in entry["terms"].items()}),
            source_feed=str(entry.get("source_feed", "")),
        )
        for entry in entries
    ]
    scored.sort(key=lambda hit: (-hit.score, hit.review_id))
    return [hit for hit in scored[:limit] if hit.score > 0.0]


def fetch_export(paths: EstatePaths) -> str:
    """Read the published CSV export.

    Raises:
        ServingRefused: when the export has been quarantined and is therefore no
            longer at its published path.
    """
    target: Path = export_path(paths)
    if not target.exists():
        raise ServingRefused(
            graph.EXPORT,
            "the published export has been quarantined and is no longer retrievable "
            "at its published path",
        )
    return target.read_text(encoding="utf-8")


__all__ = [
    "SERVED_MODEL",
    "Prediction",
    "SearchHit",
    "ServingRefused",
    "fetch_export",
    "predict",
    "search",
]
