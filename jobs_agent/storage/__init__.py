"""Persistence layer: Postgres schema, the Store, and document ids."""

from .store import (
    DOC_CANDIDATE_NAME,
    DOC_CV,
    DOC_CV_FILENAME,
    DOC_LLM_SETTINGS,
    DOC_SCORING_PROFILE,
    DOC_TEMPLATE,
    Store,
    open_store,
)

__all__ = [
    "DOC_CANDIDATE_NAME",
    "DOC_CV",
    "DOC_CV_FILENAME",
    "DOC_LLM_SETTINGS",
    "DOC_SCORING_PROFILE",
    "DOC_TEMPLATE",
    "Store",
    "open_store",
]
