"""Company careers websites as job sources.

Many graduate schemes are advertised only on the employer's own careers site,
never reaching Reed or Adzuna. A user pastes a careers URL; ``detect`` works
out which applicant-tracking system (ATS) runs it, and an adapter reads its
jobs — through the ATS's public JSON API where one exists, otherwise from the
page's schema.org JobPosting data or its job links.

Every fetch goes through ``net``: the URLs come from users, so they are
checked against SSRF before any request, and each host gets at most one
request per second.
"""
