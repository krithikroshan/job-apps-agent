"""AI analysis of postings: what each one actually asks for, whether it
mentions visa sponsorship, and how well the candidate fits."""

from .schema import AnalysisError
from .triage import BatchResult, Status, run_batch, status

__all__ = ["AnalysisError", "BatchResult", "Status", "run_batch", "status"]
