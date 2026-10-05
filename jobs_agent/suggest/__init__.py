"""AI suggestions: a scoring profile drafted from the CV or from what the
user has shortlisted and rejected, and plain-English searches turned into
queue filters. Nothing here saves anything — every suggestion is a preview
the user applies or not."""

from .profile import SuggestError, suggest_profile
from .search import SearchError, parse_search

__all__ = ["SearchError", "SuggestError", "parse_search", "suggest_profile"]
