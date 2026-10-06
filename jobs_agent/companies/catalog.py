"""Suggested companies per career preset, offered on the Companies page as
one-click adds.

Keyed by preset id (see ``presets/``); each entry is (name, careers URL).
Prefer a URL on the firm's applicant-tracking system (``*.myworkdayjobs.com``,
``boards.greenhouse.io``, ...) over its marketing careers page: those have a
JSON feed behind them, where a marketing page often shows jobs only with
JavaScript and can't be read. Adding a firm is one line here — nothing else
reads this beyond :func:`suggested_for`.
"""

from __future__ import annotations

from typing import Iterable

#: Most useful first: the Big 4, then the mid-tier firms with the largest
#: graduate intakes. Every entry was checked live (October 2026) to return
#: UK graduate roles through its adapter. Left out because their jobs can't
#: be read without a browser: EY (only Channel Islands roles come back),
#: Azets, PKF, Forvis Mazars, Moore Kingston Smith (apprenticeships only).
SUGGESTED: dict[str, list[tuple[str, str]]] = {
    "accounting_graduate": [
        ("PwC UK", "https://pwc.wd3.myworkdayjobs.com/CRM_Campus_Careers_Site"),
        ("Deloitte UK", "https://apply.deloitte.co.uk/UKEarlyCareers/SearchJobs/"),
        ("KPMG UK", "https://www.kpmgcareers.co.uk/search/vacancies/"
                    "?intakeType=Student&searchText=Graduate"),
        ("Grant Thornton UK",
         "https://ukgrantt.wd3.myworkdayjobs.com/TraineeCareersGrantThornton"),
        ("BDO UK", "https://bdouk.wd3.myworkdayjobs.com/BDO_Early_in_Career"),
        ("RSM UK", "https://www.rsmuk.com/job-search-index.json"),
        ("Menzies", "https://menzies.pinpointhq.com/"),
        ("Crowe UK", "https://isw.changeworknow.co.uk/crowe/vms/e/careers/search/new"),
        ("Saffery", "https://saffery.kallidusrecruit.com/Search.aspx"),
    ],
    "law_compliance": [],
}


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def suggested_for(preset: str, watched: Iterable[dict]) -> list[dict[str, str]]:
    """The preset's suggestions, minus any already watched — matched on URL
    or on name, since the user may have added the same firm by another link."""
    watched = list(watched)
    urls = {_norm_url(c["careers_url"]) for c in watched}
    names = {c["name"].strip().lower() for c in watched}
    return [
        {"name": name, "careers_url": url}
        for name, url in SUGGESTED.get(preset, [])
        if _norm_url(url) not in urls and name.strip().lower() not in names
    ]
