"""India location recognition and remote-eligibility logic (rule based, no network)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.textutil import norm_text

INDIAN_STATES = [
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat", "haryana",
    "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi", "new delhi", "ncr",
    "delhi ncr", "jammu and kashmir", "jammu kashmir", "ladakh", "chandigarh", "puducherry", "pondicherry",
    "andaman and nicobar", "lakshadweep", "dadra and nagar haveli", "daman and diu",
]

# city (normalised) -> state
INDIAN_CITIES: dict[str, str] = {
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka", "mysore": "Karnataka",
    "mangaluru": "Karnataka", "mangalore": "Karnataka", "hubli": "Karnataka", "hubballi": "Karnataka",
    "belagavi": "Karnataka", "mumbai": "Maharashtra", "bombay": "Maharashtra", "navi mumbai": "Maharashtra",
    "thane": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra", "nashik": "Maharashtra",
    "aurangabad": "Maharashtra", "kolhapur": "Maharashtra", "solapur": "Maharashtra",
    "hyderabad": "Telangana", "secunderabad": "Telangana", "warangal": "Telangana",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu", "tiruchirappalli": "Tamil Nadu",
    "trichy": "Tamil Nadu", "salem": "Tamil Nadu", "vellore": "Tamil Nadu", "tirunelveli": "Tamil Nadu",
    "hosur": "Tamil Nadu", "sriperumbudur": "Tamil Nadu", "delhi": "Delhi", "new delhi": "Delhi",
    "gurugram": "Haryana", "gurgaon": "Haryana", "faridabad": "Haryana", "panipat": "Haryana",
    "noida": "Uttar Pradesh", "greater noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "agra": "Uttar Pradesh", "varanasi": "Uttar Pradesh",
    "meerut": "Uttar Pradesh", "prayagraj": "Uttar Pradesh", "allahabad": "Uttar Pradesh", "bareilly": "Uttar Pradesh",
    "kolkata": "West Bengal", "calcutta": "West Bengal", "howrah": "West Bengal", "siliguri": "West Bengal",
    "durgapur": "West Bengal", "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat",
    "baroda": "Gujarat", "rajkot": "Gujarat", "gandhinagar": "Gujarat", "bharuch": "Gujarat", "vapi": "Gujarat",
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan", "kota": "Rajasthan", "ajmer": "Rajasthan",
    "bhiwadi": "Rajasthan", "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh", "jabalpur": "Madhya Pradesh",
    "gwalior": "Madhya Pradesh", "ujjain": "Madhya Pradesh", "patna": "Bihar", "gaya": "Bihar",
    "ranchi": "Jharkhand", "jamshedpur": "Jharkhand", "dhanbad": "Jharkhand", "bokaro": "Jharkhand",
    "raipur": "Chhattisgarh", "bhilai": "Chhattisgarh", "bhubaneswar": "Odisha", "cuttack": "Odisha",
    "rourkela": "Odisha", "guwahati": "Assam", "dehradun": "Uttarakhand", "haridwar": "Uttarakhand",
    "chandigarh": "Chandigarh", "mohali": "Punjab", "panchkula": "Haryana", "ludhiana": "Punjab",
    "amritsar": "Punjab", "jalandhar": "Punjab", "kochi": "Kerala", "cochin": "Kerala", "ernakulam": "Kerala",
    "thiruvananthapuram": "Kerala", "trivandrum": "Kerala", "kozhikode": "Kerala", "calicut": "Kerala",
    "thrissur": "Kerala", "visakhapatnam": "Andhra Pradesh", "vizag": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh", "guntur": "Andhra Pradesh", "tirupati": "Andhra Pradesh",
    "amaravati": "Andhra Pradesh", "panaji": "Goa", "margao": "Goa", "shimla": "Himachal Pradesh",
    "solan": "Himachal Pradesh", "baddi": "Himachal Pradesh", "jammu": "Jammu and Kashmir",
    "srinagar": "Jammu and Kashmir", "pondicherry": "Puducherry", "puducherry": "Puducherry",
    "gift city": "Gujarat", "pithampur": "Madhya Pradesh", "manesar": "Haryana", "chakan": "Maharashtra",
}

NON_INDIA_COUNTRIES = [
    "united states", "usa", "u s a", "us", "u s", "america", "united kingdom", "uk", "u k", "england", "scotland",
    "canada", "germany", "france", "spain", "italy", "netherlands", "ireland", "poland", "portugal", "sweden",
    "norway", "denmark", "finland", "switzerland", "austria", "belgium", "czech republic", "romania", "hungary",
    "greece", "turkey", "ukraine", "lithuania", "estonia", "latvia", "bulgaria", "serbia", "croatia",
    "singapore", "malaysia", "indonesia", "philippines", "thailand", "vietnam", "japan", "china", "hong kong",
    "taiwan", "south korea", "korea", "australia", "new zealand", "united arab emirates", "uae", "dubai",
    "abu dhabi", "saudi arabia", "qatar", "kuwait", "bahrain", "oman", "israel", "egypt", "south africa",
    "nigeria", "kenya", "brazil", "mexico", "argentina", "chile", "colombia", "peru", "pakistan", "bangladesh",
    "sri lanka", "nepal", "europe", "emea", "latam", "north america", "dach", "nordics", "mena", "gcc",
    "london", "new york", "san francisco", "seattle", "austin", "boston", "chicago", "toronto", "vancouver",
    "berlin", "paris", "amsterdam", "dublin", "sydney", "melbourne", "tokyo", "seoul", "riyadh", "doha",
]
# Words that make a location vague rather than non-India
VAGUE_LOCATIONS = {"global", "worldwide", "anywhere", "apac", "asia", "asia pacific", "multiple locations",
                   "various", "multiple", "remote", "work from home", "wfh", "hybrid", "flexible"}

REMOTE_RE = re.compile(r"\b(remote|work(?:ing)? from home|wfh|work from anywhere|telecommut\w*|fully distributed)\b", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|work from office|wfo)\b", re.I)

# Evidence in a description that a remote role is open to India
INDIA_OK_RE = re.compile(
    r"(open to|available (?:to|for|in)|hiring (?:in|from)|candidates?(?: based)? (?:in|from)|based in|located in|"
    r"residing in|reside in|eligible (?:in|to work in)|work(?:ing)? from)[^.\n]{0,60}\bindia\b|"
    r"\bindia[- ]based\b|\bremote\s*[-–:,(/]?\s*india\b|\bindia\s*[-–:,(/]?\s*remote\b|\bpan[- ]india\b", re.I)
# Evidence that a remote role is restricted to somewhere that is not India
RESTRICT_RE = re.compile(
    r"\b(us|u\.s\.|usa|uk|eu|emea|canada|europe|latam|north america)[- ]?(only|based|residents?|citizens?)\b|"
    r"(must|should|need to)\s+(be\s+)?(located|based|reside|living|resident)[^.\n]{0,40}\b(us|u\.s\.|usa|united states|uk|europe|canada|eu)\b|"
    r"authori[sz]ed to work in the (us|u\.s\.|united states|uk|eu|canada)|"
    r"\bright to work in the (uk|eu|us)\b|\bremote\s*[-–:,(/]\s*(us|usa|united states|uk|canada|europe|emea|latam|germany|france|spain|poland)\b", re.I)


@dataclass
class GeoResult:
    is_india: bool | None          # True / False / None (unknown)
    city: str = ""
    state: str = ""
    country: str = ""
    remote: bool = False
    hybrid: bool = False
    onsite: bool = False
    note: str = ""


def _parts(location: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;|/•\n]| or | and ", location or "") if p.strip()]


def parse_location(location: str, title: str = "") -> GeoResult:
    """Classify a free-text location string."""
    loc = location or ""
    remote = bool(REMOTE_RE.search(loc) or REMOTE_RE.search(title or ""))
    hybrid = bool(HYBRID_RE.search(loc) or HYBRID_RE.search(title or ""))
    onsite = bool(ONSITE_RE.search(loc))
    india_hit = False
    other_hit = False
    city = state = ""
    for part in _parts(loc) or [loc]:
        for chunk in [c.strip() for c in part.split(",") if c.strip()] or [part]:
            n = norm_text(re.sub(r"\b(remote|hybrid|on[- ]?site|wfh|work from home)\b", " ", chunk, flags=re.I))
            if not n:
                continue
            if n in ("india", "in", "ind", "bharat", "republic of india"):
                india_hit = True
            elif n in INDIAN_CITIES:
                india_hit = True
                city = city or n.title()
                state = state or INDIAN_CITIES[n]
            elif n in INDIAN_STATES:
                india_hit = True
                state = state or n.title()
            elif n in NON_INDIA_COUNTRIES:
                other_hit = True
            else:
                # multi-word chunk such as "Bengaluru Karnataka" or "Remote Mumbai"
                for tok in sorted(INDIAN_CITIES, key=len, reverse=True):
                    if re.search(rf"\b{re.escape(tok)}\b", n):
                        india_hit = True
                        city = city or tok.title()
                        state = state or INDIAN_CITIES[tok]
                        break
                else:
                    for tok in INDIAN_STATES:
                        if re.search(rf"\b{re.escape(tok)}\b", n):
                            india_hit = True
                            state = state or tok.title()
                            break
                    else:
                        for tok in NON_INDIA_COUNTRIES:
                            if len(tok) > 3 and re.search(rf"\b{re.escape(tok)}\b", n):
                                other_hit = True
                                break
    if india_hit:
        return GeoResult(True, city, state, "India", remote, hybrid, onsite,
                         "India location" + (" (also lists non-India places)" if other_hit else ""))
    if other_hit:
        return GeoResult(False, "", "", "", remote, hybrid, onsite, "non-India location")
    return GeoResult(None, "", "", "", remote, hybrid, onsite, "location unknown or vague")


def work_arrangement(geo: GeoResult, text_head: str = "") -> str:
    if geo.remote and not geo.hybrid:
        return "Remote"
    if geo.hybrid:
        return "Hybrid"
    if geo.onsite:
        return "On-site"
    if REMOTE_RE.search(text_head or "") and not HYBRID_RE.search(text_head or ""):
        return "Remote"  # only description says so; flagged by eligibility logic
    if HYBRID_RE.search(text_head or ""):
        return "Hybrid"
    return "On-site" if geo.is_india else "Unknown"


def india_eligibility(geo: GeoResult, description: str, hint_country: str = "",
                      hint_remote: bool | None = None) -> tuple[str, str]:
    """Return (``Yes`` | ``No`` | ``Unclear``, reason). The 'India Remote Eligibility' column.

    Rules (from the project brief): a remote job must explicitly allow India-based candidates or have an
    India-eligible location; remote is never assumed to be worldwide.
    """
    head = (description or "")[:2500]
    is_remote = geo.remote or bool(hint_remote)
    if geo.is_india:
        return "Yes", "India location"
    if geo.is_india is False:
        if is_remote and INDIA_OK_RE.search(head):
            return "Yes", "non-India location listed but description says open to India"
        return "No", "location is outside India"
    # unknown / vague location
    if is_remote or REMOTE_RE.search(head[:600]):
        if RESTRICT_RE.search(head) and not INDIA_OK_RE.search(head):
            return "No", "remote role restricted to another region"
        if INDIA_OK_RE.search(head):
            return "Yes", "description states India candidates are eligible"
        # NOTE: an India-scoped search is deliberately NOT accepted as proof for a remote role:
        # remote jobs are never assumed to be open to candidates residing in India.
        return "Unclear", "remote role with no stated India eligibility"
    if hint_country.lower() == "india":
        return "Yes", "found through an India-scoped search"
    if INDIA_OK_RE.search(head):
        return "Yes", "description mentions India"
    return "Unclear", "location not stated"
