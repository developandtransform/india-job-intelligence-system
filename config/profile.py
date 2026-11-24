"""Search profile: target functions, search queries and skill keywords.

This is *data*, not credentials, so it lives next to the code. Edit it freely (see docs/FILTERING_LOGIC.md).
"""
from __future__ import annotations

# function -> (is_core_for_PGDM, [keywords that may appear in a job TITLE or description])
# Matching is case-insensitive on whole words / phrases.
FUNCTIONS: dict[str, tuple[bool, list[str]]] = {
    "Marketing": (True, ["marketing", "marketer", "campaign", "go-to-market", "gtm", "content marketing", "seo", "sem",
                         "performance marketing", "social media", "e-commerce marketing", "trade marketing",
                         "category marketing", "marcom", "marketing communications"]),
    "Digital Marketing": (True, ["digital marketing", "digital marketer", "performance marketing", "paid media",
                                 "seo", "sem", "social media", "growth marketing", "email marketing", "ppc",
                                 "digital strategist", "performance marketer"]),
    "Brand Management": (True, ["brand manager", "brand management", "brand executive", "brand strategy",
                                "assistant brand manager", "associate brand manager", "brand associate", "brand"]),
    "Product Marketing": (True, ["product marketing", "product marketer", "pmm", "market positioning"]),
    "Sales": (True, ["sales", "account executive", "inside sales", "field sales", "key account", "channel sales",
                     "territory", "relationship manager", "area sales", "sales officer", "sales trainee"]),
    "Business Development": (True, ["business development", "biz dev", "bd executive", "bdm", "bde",
                                    "lead generation", "new business", "business growth"]),
    "Finance": (True, ["finance", "financial", "accounts", "accounting", "fp&a", "fpa", "treasury", "controller",
                       "audit", "taxation", "credit", "risk analyst", "commercial finance"]),
    "Corporate Finance": (True, ["corporate finance", "financial planning", "fp&a", "mergers", "m&a", "valuation",
                                 "investor relations", "capital markets", "business finance", "finance business partner"]),
    "Financial Analysis": (True, ["financial analyst", "finance analyst", "financial analysis", "equity analyst",
                                  "credit analyst", "investment analyst", "financial modelling", "financial modeling"]),
    "Investment Research": (True, ["investment research", "equity research", "research analyst", "portfolio",
                                   "asset management", "wealth", "fund", "investment banking", "private equity"]),
    "Banking": (True, ["banking", "bank", "branch", "relationship manager", "credit officer", "loan", "lending",
                       "probationary officer", "credit manager", "retail banking", "corporate banking", "treasury"]),
    "Fintech": (True, ["fintech", "payments", "lending product", "digital lending", "wealthtech", "insurtech",
                       "kyc", "merchant onboarding"]),
    "Human Resources": (True, ["human resources", "hr ", "hr-", "hrbp", "talent acquisition", "recruiter",
                               "recruitment", "learning and development", "l&d", "people operations", "payroll",
                               "employee engagement", "talent management", "compensation and benefits", "hr executive",
                               "hr generalist", "hr intern"]),
    "HR Analytics": (False, ["hr analytics", "people analytics", "workforce analytics", "hr data", "hris"]),
    "Business Analytics": (True, ["business analyst", "business analytics", "analytics", "insights", "mis analyst",
                                  "mis executive", "reporting analyst", "decision science"]),
    "Data Analytics": (True, ["data analyst", "data analytics", "data analysis", "sql", "power bi", "tableau",
                              "data insights", "analytics associate", "quantitative analyst", "data science intern"]),
    "Business Intelligence": (False, ["business intelligence", "bi analyst", "bi developer", "dashboard", "power bi",
                                      "tableau", "looker", "reporting"]),
    "Media": (False, ["media planning", "media buying", "media planner", "media executive", "media strategist",
                      "broadcast", "journalist", "editorial", "content writer"]),
    "Advertising": (False, ["advertising", "ad sales", "account planning", "creative strategy", "media agency",
                            "client servicing", "account management"]),
    "Public Relations": (False, ["public relations", "pr executive", "pr manager", "media relations", "press",
                                 "corporate affairs", "reputation"]),
    "Communications": (False, ["communications", "corporate communication", "internal communication",
                               "communication specialist", "content strategist", "copywriter"]),
    "Logistics": (True, ["logistics", "transportation", "warehouse", "warehousing", "distribution", "freight",
                         "fleet", "last mile", "3pl", "dispatch"]),
    "Supply Chain Management": (True, ["supply chain", "demand planning", "supply planning", "s&op", "inventory",
                                       "scm", "materials planning", "production planning", "sourcing"]),
    "Procurement": (True, ["procurement", "purchase", "purchasing", "buyer", "category manager", "vendor management",
                           "strategic sourcing", "supplier"]),
    "Operations": (True, ["operations", "operation executive", "ops", "process excellence", "process improvement",
                          "plant", "manufacturing operations", "service delivery", "operations analyst",
                          "operations manager"]),
    "General Management": (False, ["general management", "gm trainee", "business head", "management associate",
                                   "chief of staff", "founder's office", "founders office", "business manager"]),
    "Management Trainee": (True, ["management trainee", "mt program", "graduate trainee", "graduate engineer trainee",
                                  "management associate", "leadership development", "campus hire", "gt program",
                                  "associate program", "rotational program", "future leaders", "trainee"]),
    "Strategy": (True, ["strategy", "strategic", "corporate strategy", "business strategy", "strategy analyst",
                        "strategic initiatives", "transformation", "chief of staff"]),
    "Consulting": (True, ["consultant", "consulting", "advisory", "associate consultant", "business consultant",
                          "management consulting", "analyst - consulting"]),
    "Business Operations": (True, ["business operations", "biz ops", "bizops", "business ops", "revenue operations",
                                   "sales operations", "strategy and operations", "business excellence"]),
    "Project Management": (False, ["project manager", "project management", "project coordinator", "pmo",
                                   "program manager", "programme manager", "project executive", "scrum master",
                                   "delivery manager"]),
    "Customer Success": (False, ["customer success", "client success", "account management", "customer experience",
                                 "customer relations", "client relationship", "customer onboarding"]),
    "Market Research": (True, ["market research", "consumer insights", "research executive", "research associate",
                               "competitive intelligence", "market intelligence", "research analyst", "survey"]),
    "Growth": (False, ["growth", "growth manager", "growth associate", "user acquisition", "retention",
                       "growth hacker", "growth marketing"]),
    "Partnerships": (False, ["partnerships", "partnership manager", "alliances", "channel partner",
                             "strategic partnerships", "partner management", "ecosystem"]),
    "Other Management": (False, ["management", "executive", "associate", "coordinator", "officer", "analyst",
                                 "specialist", "administrator"]),
}

# Titles that are clearly NOT in scope. Only used when no function keyword matched in the title.
IRRELEVANT_TITLE_PATTERNS = [
    r"\b(software|backend|frontend|front-end|back-end|full[- ]?stack|devops|sre|qa|sdet|android|ios|embedded|firmware|"
    r"machine learning|ml engineer|data engineer|cloud engineer|network engineer|security engineer|java|python|"
    r"golang|\.net|react|angular|node\.?js|php|salesforce developer|sap abap|mainframe|dba|database administrator)\b",
    r"\b(nurse|doctor|physician|pharmacist|teacher|lecturer|professor|driver|chauffeur|electrician|plumber|welder|"
    r"mechanic|technician|security guard|housekeeping|chef|cook|waiter|barista|cashier|receptionist|telecaller|"
    r"data entry|tele[- ]?sales|call centre|call center|bpo|voice process|non[- ]?voice|delivery (boy|partner|executive)|"
    r"warehouse (helper|associate)|packer|loader|picker)\b",
    r"\b(graphic designer|ui/ux|ux designer|ui designer|video editor|animator|3d artist|architect|civil engineer|"
    r"mechanical engineer|electrical engineer|site engineer|structural)\b",
]

# Skills typical of PGDM/MBA entry roles. Used for the "skills match" component of the score.
SKILL_KEYWORDS = [
    "excel", "advanced excel", "sql", "power bi", "tableau", "python", "powerpoint", "google analytics", "crm",
    "salesforce", "financial modelling", "financial modeling", "valuation", "budgeting", "forecasting", "p&l",
    "market research", "seo", "sem", "google ads", "social media", "content", "brand", "campaign", "analytics",
    "stakeholder", "presentation", "communication", "negotiation", "supply chain", "procurement", "sap",
    "erp", "six sigma", "lean", "project management", "mis", "reporting", "business analysis", "consulting",
    "strategy", "recruitment", "hris", "payroll", "kpi", "dashboard", "data analysis", "b2b", "lead generation",
]

# (search term, kind). kind: fulltime | internship.  Queries are rotated daily (see JOBSPY_MAX_QUERIES_PER_RUN).
SEARCH_QUERIES: list[tuple[str, str]] = [
    ("management trainee", "fulltime"),
    ("graduate trainee", "fulltime"),
    ("marketing executive", "fulltime"),
    ("digital marketing", "fulltime"),
    ("brand manager associate", "fulltime"),
    ("product marketing associate", "fulltime"),
    ("business development executive", "fulltime"),
    ("sales executive fresher", "fulltime"),
    ("financial analyst", "fulltime"),
    ("investment research analyst", "fulltime"),
    ("credit analyst", "fulltime"),
    ("banking relationship manager", "fulltime"),
    ("fintech associate", "fulltime"),
    ("hr executive", "fulltime"),
    ("talent acquisition executive", "fulltime"),
    ("hr analytics", "fulltime"),
    ("business analyst", "fulltime"),
    ("data analyst", "fulltime"),
    ("business intelligence analyst", "fulltime"),
    ("public relations executive", "fulltime"),
    ("corporate communications", "fulltime"),
    ("media planning executive", "fulltime"),
    ("supply chain analyst", "fulltime"),
    ("logistics executive", "fulltime"),
    ("procurement executive", "fulltime"),
    ("operations executive", "fulltime"),
    ("business operations associate", "fulltime"),
    ("strategy analyst", "fulltime"),
    ("consulting analyst", "fulltime"),
    ("project coordinator", "fulltime"),
    ("customer success associate", "fulltime"),
    ("market research analyst", "fulltime"),
    ("growth associate", "fulltime"),
    ("partnerships associate", "fulltime"),
    ("marketing intern", "internship"),
    ("finance intern", "internship"),
    ("hr intern", "internship"),
    ("business analyst intern", "internship"),
    ("data analytics intern", "internship"),
    ("supply chain intern", "internship"),
    ("operations intern", "internship"),
    ("sales business development intern", "internship"),
    ("consulting strategy intern", "internship"),
    ("market research intern", "internship"),
]

# Words in a title/description that mark a startup self-description (see company_verification)
STARTUP_LANGUAGE = [r"\bstart-?up\b", r"\bearly[- ]stage\b", r"\bseed[- ]funded\b", r"\bseries [a-e]\b",
                    r"\bventure[- ]backed\b", r"\bfast[- ]growing (?:start-?up|company)\b", r"\bunicorn\b",
                    r"\bsoonicorn\b", r"\bbootstrapped\b"]

AGGREGATOR_DOMAINS = ["naukri.com", "linkedin.com", "indeed.com", "glassdoor.co.in", "glassdoor.com", "google.com",
                      "iimjobs.com", "hirist.tech", "hirist.com", "shine.com", "foundit.in", "timesjobs.com",
                      "internshala.com", "ziprecruiter.com", "apify.com"]
