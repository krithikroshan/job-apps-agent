"""London legal and compliance roles for a law graduate — the profile this
tool was first built around."""

from dataclasses import replace

from ..profile import DEFAULT_PROFILE

LABEL = "Legal & compliance (graduate, London)"
DESCRIPTION = ("Compliance, financial crime, KYC and paralegal roles for a law "
               "graduate. Blocks qualified-solicitor and senior roles.")

PROFILE = replace(
    DEFAULT_PROFILE,
    preset="law_compliance",
    job_category="legal",
    target_titles={
        # Compliance / financial crime
        "compliance analyst": 30,
        "compliance officer": 30,
        "compliance associate": 30,
        "compliance assistant": 26,
        "compliance monitoring": 26,
        "regulatory compliance": 28,
        "financial crime": 28,
        "aml analyst": 28,
        "kyc analyst": 26,
        "know your customer": 24,
        "client onboarding": 22,
        "regulatory reporting": 22,
        "risk and compliance": 24,
        # Legal support
        "paralegal": 26,
        "legal assistant": 22,
        "legal analyst": 24,
        "legal counsel assistant": 22,
        "contracts administrator": 18,
        "legal operations": 18,
        "document review": 16,
        # Graduate/entry framing
        "legal intern": 20,
        "graduate compliance": 26,
        "trainee compliance": 26,
    },
)
