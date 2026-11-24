# Data dictionary

## Tabs
`All Jobs` (master, permanent) · generated views: `New Today`, `Strong Matches`, `Possible Matches`, `Paid Internships`, `Salary Undisclosed`, `Salary Verification Required`, `Company Verification Required`, `Experience Verification Required`, `Location Verification Required` (extra), `Applied`, `Rejected and Closed` · `Source Statistics` and `System Logs` (append-only) · `Company Registry` (extra tab, you edit it).

View tabs are overwritten on every run: **edit Application Status and Notes only in `All Jobs`.** Views show at most 2,000 rows each.

## `All Jobs` columns
Columns A and B are yours; the program never writes them for existing rows. The columns marked *hidden* are internal and hidden by default on newly created sheets.

| # | Column | Meaning |
|---|---|---|
| 1 | Application Status | YOU: New/Saved/Applied/Interview/Rejected/Offer/Closed (dropdown) |
| 2 | Notes | YOU: free text |
| 3 | Job Title | Cleaned title |
| 4 | Company | Employer name |
| 5 | Category | Result of the rules (see FILTERING_LOGIC.md) |
| 6 | Match Score | 0-100 ranking score; never overrides a rule |
| 7 | Location | As published |
| 8 | CTC LPA | Annual CTC in lakhs, only when a real figure was found |
| 9 | Monthly Stipend INR | Internships only |
| 10 | Experience Text | Normalised experience requirement |
| 11 | Full-time/Internship | Full-time, Internship or Other |
| 12 | Posted Date | ISO date if published |
| 13 | Direct Apply URL | Employer/apply link if known |
| 14 | Job URL | Listing link |
| 15 | Match Explanation | Why it scored as it did |
| 16 | Exclusion Reason | Why Excluded |
| 17 | Verification Flags | What needs checking by hand |
| 18 | Job Status | Active / Possibly Closed / Closed |
| 19 | Source | Source(s); first is the primary |
| 20 | All Source URLs | Every link where the job was seen |
| 21 | Function | Detected job function |
| 22 | Company Classification | Startup / MNC / Large Indian company / ... (from registry or source data) |
| 23 | Startup Verification Status | Startup - meets/fails criteria, Not a startup, Verification Required |
| 24 | Company Evidence | Where the classification came from |
| 25 | Company Website | If provided |
| 26 | Company Founding Year | If known |
| 27 | Company Age | Computed from today's date |
| 28 | Employee Count | If known |
| 29 | Country | Parsed |
| 30 | City | Parsed |
| 31 | State | Parsed |
| 32 | Work Arrangement | On-site / Hybrid / Remote |
| 33 | India Remote Eligibility | Yes / No / Unclear |
| 34 | Experience Minimum | Extracted years |
| 35 | Experience Maximum | Extracted years (blank = open-ended) |
| 36 | Experience Verification Status | Eligible / Excluded / Verification Required |
| 37 | Experience Source | structured, text pattern, or gemini (quote verified) |
| 38 | Employment Type | Raw type from source |
| 39 | Salary Minimum | As published |
| 40 | Salary Maximum | As published |
| 41 | Salary Currency | As published |
| 42 | Salary Period | year / month / ... |
| 43 | Salary Basis | CTC / Fixed-Base / Variable / OTE / Unspecified (never mixed up) |
| 44 | Salary Disclosure Status | Disclosed / Not disclosed / Unpaid |
| 45 | Salary Verification Status | Full-time pay verdict |
| 46 | Stipend Verification Status | Internship pay verdict |
| 47 | CTC LPA Min *(hidden)* | Numeric, for re-evaluation (hidden) |
| 48 | CTC LPA Max *(hidden)* | Numeric (hidden) |
| 49 | Stipend INR Min *(hidden)* | Numeric (hidden) |
| 50 | Stipend INR Max *(hidden)* | Numeric (hidden) |
| 51 | Application Deadline | If published |
| 52 | Date Discovered | First run that saw it |
| 53 | First Seen Date |  |
| 54 | Last Seen Date | Last run that saw it |
| 55 | Last Updated Date | Last material change |
| 56 | Responsibilities | Extracted section |
| 57 | Required Skills | Extracted section |
| 58 | Preferred Skills | Extracted section |
| 59 | Job Description | Truncated to 3,000 characters |
| 60 | Internal Job ID | Stable hash id - do not edit |
| 61 | Original Job ID | Source's id |
| 62 | Duplicate Group ID *(hidden)* | Group of merged duplicates (hidden) |
| 63 | Source Keys *(hidden)* | source:id pairs (hidden) |
| 64 | Source Scopes *(hidden)* | Boards that listed it (hidden, drives closure) |
| 65 | Repost Count | Times the same posting reappeared after closing |
| 66 | Missed Runs *(hidden)* | Consecutive complete fetches without it (hidden) |
| 67 | Content Hash *(hidden)* | Fingerprint of material fields (hidden) |
| 68 | Last Reported Hash *(hidden)* | What the last e-mail contained (hidden) |
| 69 | Last Reported Date *(hidden)* | (hidden) |
| 70 | Raw Data *(hidden)* | Small JSON of source extras (hidden) |

## Other tabs
- **Source Statistics**: one row per connector per run (status, jobs fetched, new, dropped, requests, cost USD, seconds, errors).
- **System Logs**: INFO+ log lines of the run (secrets redacted).
- **Company Registry**: Company, Classification, Founded Year, Employee Count, Evidence / Source URL, Verified By, Notes, Last Updated (used as the verification date), Verification Status (Verified = evidence URL present; otherwise Unverified / User-provided). Unknown companies are added automatically with blank facts; fill them in and the next run uses them.
