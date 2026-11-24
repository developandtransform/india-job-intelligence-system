"""Invented sample postings used ONLY by `python main.py demo` and the tests. Companies and pay are fictional."""
from __future__ import annotations

from connectors.base import Connector, ConnectorResult
from core.models import Job


def _j(i: str, title: str, company: str, location: str, desc: str, **kw) -> Job:
    return Job(source="demo", original_id=i, title=title, company=company, location=location, full_description=desc,
               job_url=f"https://example.com/jobs/{i}", hint_country="India" if "India" in location or "," in location else "", **kw)


def sample_jobs() -> list[Job]:
    return [
        _j("1", "Business Development Executive", "Infosys", "Pune, Maharashtra, India",
           "Experience: 1-3 years. CTC: 9-12 LPA. Lead generation, client outreach, CRM."),
        _j("2", "Senior Marketing Manager", "Tata Consultancy Services", "Mumbai, India",
           "Requires 5+ years of experience in digital marketing. CTC 18 LPA."),
        _j("3", "Marketing Analyst", "Infosys", "Bengaluru, India",
           "0-2 years experience. Salary range 6 to 10 LPA. SEO, campaigns, analytics."),
        _j("4", "Sales Intern", "HDFC Bank", "Mumbai, India",
           "Internship, 3 months. Freshers welcome. Stipend Rs 15,000 per month. Sales support and lead generation."),
        _j("5", "Operations Intern", "Wipro", "Hyderabad, India",
           "Unpaid internship for 2 months. Freshers welcome. Operations support."),
        _j("6", "Growth Associate", "Tiny Startup Pvt Ltd", "Delhi, India",
           "1-2 years experience. CTC 10 LPA. Own growth experiments at our early-stage startup."),
        _j("7", "Account Executive", "Accenture", "Gurugram, India",
           "Join our sales team. Competitive salary. Prior experience preferred."),
        _j("8", "Product Marketing Manager", "Infosys", "Remote - US",
           "2 years experience. Remote within the United States only. CTC 20 LPA."),
        _j("9", "Operations Executive", "Infosys", "Chennai, India",
           "2-3 years experience. Salary not disclosed. Supply chain coordination, vendor management."),
        _j("10", "Business Analyst", "Infosys", "Remote",
           "Fully remote role. 1-2 years experience. CTC 12 LPA. Reports, dashboards and SQL."),
        _j("11", "Finance Analyst", "Accenture", "Bengaluru, India",
           "1-2 years experience. Salary: up to 12 LPA. FP&A, reporting."),
    ]


class DemoConnector(Connector):
    name = "demo"

    def enabled(self) -> bool:
        return True

    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        result.jobs = sample_jobs()
        result.raw_count = len(result.jobs)
        result.note = "built-in sample data (not real postings)"
