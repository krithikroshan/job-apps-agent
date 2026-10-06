"""Paralegal roles in London for a law graduate or someone with up to about
six months' experience — paralegal titles only, nothing adjacent."""

from ..profile import Profile

LABEL = "Paralegal (graduate or junior, London)"
DESCRIPTION = ("Paralegal roles only, in London, for a law graduate or up to "
               "six months' experience. Blocks senior paralegals and roles "
               "asking for two or more years.")

#: Years of experience that put a role out of reach. One year is left in:
#: paralegal adverts ask for it loosely and often take a strong graduate.
_TOO_MANY_YEARS = [("2", "two"), ("3", "three"), ("4", "four"), ("5", "five")]

PROFILE = Profile(
    name="",
    preset="paralegal_london",
    locations=["London"],
    radius_miles=15,
    job_category="legal",
    # Every title contains "paralegal", so anything else — legal assistant,
    # compliance, trainee solicitor — fails the title match and is dropped.
    # The variants are listed for the searches they run, not for scoring.
    target_titles={
        "graduate paralegal": 30,
        "junior paralegal": 30,
        "trainee paralegal": 30,
        "paralegal assistant": 28,
        "paralegal": 26,
    },
    domain_terms={
        "law graduate": 10,
        "recent graduate": 8,
        "graduate": 6,
        "entry level": 8,
        "no experience": 8,
        "full training": 6,
        "6 months": 6,
        "six months": 6,
        "llb": 8,
        "gdl": 6,
        "pgdl": 6,
        "lpc": 6,
        "sqe": 8,
        "qwe": 8,
        "qualifying work experience": 8,
        "training contract": 6,
        "legal research": 6,
        "drafting": 5,
        "document review": 5,
        "disclosure": 4,
        "bundles": 4,
        "case management": 4,
        "litigation": 4,
    },
    title_blockers=[
        "head of",
        "director",
        "senior",
        "lead ",
        "principal",
        "manager",
        "supervisor",
        "team leader",
        "partner",
        "chief",
        "experienced",
        "solicitor",  # "Solicitor / Paralegal" hybrids want the qualification
        "legal executive",
    ],
    experience_blockers=[
        *(phrase
          for digit, word in _TOO_MANY_YEARS
          for phrase in (
              f"{digit}+ years",
              f"{digit} years' experience",
              f"{digit} years experience",
              f"{digit} years of experience",
              f"{word} years' experience",
              f"{word} years experience",
              f"{word} years of experience",
              f"at least {digit} years",
              f"at least {word} years",
              f"minimum of {digit} years",
              f"minimum of {word} years",
          )),
        "qualified solicitor",
        "nq solicitor",
        "must be sra",
        "fully qualified",
    ],
    # London paralegal pay: roughly £25-32k for a graduate.
    salary_bands=[[30_000, 10], [26_000, 5], [22_000, 0], [0, -8]],
    # Contract document-review and disclosure work hires fast and counts as
    # experience, so it's worth a nudge.
    contract_bonus=8,
    domain_only_threshold=0,
)
