"""Watched companies: employers' own careers sites as a job source.

Graduate schemes are often advertised only on the firm's own site. A user
adds a careers URL on the Companies page; each check reads that site (via
``careers/``) for UK roles matching the profile's target titles, and stages
them in the same queue as job-board postings — scored, deduplicated and
reviewed exactly the same way. ``catalog`` suggests firms per preset.
"""

#: What to call each applicant-tracking system the detector recognises.
ATS_LABELS = {
    "workday": "Workday",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "lever-eu": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "pinpoint": "Pinpoint",
    "json-index": "Job index",
    "generic": "Careers page",
}


def ats_label(ats: str) -> str:
    return ATS_LABELS.get(ats, ats.title() if ats else "Careers page")


__all__ = ["ATS_LABELS", "ats_label"]
