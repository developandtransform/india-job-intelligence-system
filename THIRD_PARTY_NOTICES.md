# Third-party notices

| Work | Licence | How it is used |
|---|---|---|
| [JobSpy](https://github.com/speedyapply/JobSpy) (`python-jobspy`) | MIT | Installed as a dependency; called from `connectors/jobspy_connector.py`. No source copied |
| [GetAnything-1/ats-jobs-scraper](https://github.com/GetAnything-1/ats-jobs-scraper) | MIT | Public ATS endpoint patterns and Workday paging/throttle handling were **adapted** and rewritten in `connectors/ats_connector.py`. Credit to its authors. The full MIT text of that project applies to the adapted ideas: permission to use, copy, modify and distribute is granted provided the copyright and permission notice of the original are retained |
| career-page-job-monitor, sukhhdipsingh/ats-job-scraper, JobBot | no licence file | **Reference only.** No code was copied. JobBot's auto-apply is deliberately not built |

Python dependencies (requests, urllib3, beautifulsoup4, gspread, google-auth, tenacity, rapidfuzz, PyYAML, python-dotenv, tzdata, pytest) keep their own licences; install them from PyPI.

Details of the repository research: [docs/REPOSITORY_AUDIT.md](docs/REPOSITORY_AUDIT.md).
