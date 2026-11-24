# Filtering logic

## Category precedence (first match wins)
1. **Excluded** (any hard rule failed)
2. **Experience Verification Required**
3. **Location Verification Required**
4. **Company Verification Required**
5. **Salary Verification Required**
6. **Salary Undisclosed**
7. **Paid Internships** (internship that passed)
8. **Strong Matches** (score >= 70) / **Possible Matches** (>= 45); below 45 = Excluded as "Irrelevant position"

A high score never rescues a job that failed a rule.

## Scope and location
- Explicit foreign locations are **dropped** (counted in *Source Statistics*, not stored unless `STORE_OUT_OF_SCOPE_JOBS=true`). Mentioning India in the description does not rescue them.
- A **remote** job qualifies only if it states it is open to candidates in India ("Remote, India", "open to candidates in India"). An India-scoped search is **not** accepted as proof. Remote or vague locations with no stated eligibility go to **Location Verification Required** (stored, shown in their own tab and e-mail section).
- Indian cities/states, hybrid in India and relocation within India qualify.
- Only Full-time and Internship are supported. Titles in an unrelated function (e.g. chef) are out of scope (`config/profile.py`).

## Experience (max 3 years, all jobs)
A structured field from the source wins, else patterns in the text. Tested examples: 0, 0-1, 0-2, 0-3, 1-2, 1-3, 2-3 years and "freshers" pass; `2-4`, `3-5`, `3+`, `4+`, `5+`, `0-5` are Excluded; nothing stated, conflicting or open-ended statements ("minimum 2 years") = Experience Verification Required. Months are converted. Durations of internships/contracts/probation and company-history years ("25 years of excellence") are not experience. **Titles never infer eligibility** (Junior, Associate, Executive, Trainee, Intern); an internship with no stated requirement goes to verification.

## Full-time pay (CTC >= 8 LPA)
Lowest stated figure >= 8 = pass; entire range below = Excluded; range crossing 8 (6-10) = Salary Verification Required; no figure = Salary Undisclosed. "Up to X" is a **maximum**: below the threshold = Excluded, otherwise verification (the minimum is unknown). Labelled fixed / base / in-hand / variable / OTE figures and **monthly** figures are not CTC: verification. Unlabelled annual figures such as "9 LPA" are treated as CTC (Indian listing convention) and recorded as basis "CTC". Foreign currencies are not converted. Salaries are never estimated.

## Internships (stipend >= Rs 10,000/month)
10,000 or more = pass; below or unpaid = Excluded; range crossing 10,000 = Salary Verification Required; "up to X" = maximum (verification unless below the threshold); not stated or period unclear = Salary/Stipend Undisclosed. Weekly, yearly and total stipends are converted only when the duration is stated.

## Companies
- **Startup**: eligible only if age >= 5 years **and** >= 90 employees. Age is computed from today's date; with only a founding year the age is a range, and anything not provably >= 5 is Verification Required (provably younger = Excluded). Unknown headcount = Verification Required; below 90 = Excluded.
- **Everything else** (MNC, large Indian company, SME, bank, consulting firm, FMCG, ...): no age or headcount rule, even with 20 employees.
- **Unknown company => Company Verification Required.** A name containing "Bank" or "Consulting" is only a hint and does not unlock eligibility. Not every small or new business is called a startup.
- Evidence order: *Company Registry* tab (yours) > bundled seed > structured data supplied by a source (e.g. a source-provided company type or founding year/headcount).
- **The bundled seed is UNVERIFIED** (234 entries, no recorded evidence). In the default `COMPANY_SEED_MODE=flag` a match is used but reported as "Not a startup (unverified)" with a visible warning on the job; `strict` ignores the seed for eligibility so those jobs go to Company Verification Required. A registry entry counts as *Verified* only with an evidence URL (and, in the seed file, a verification date).

## Score (0-100, explains itself in *Match Explanation*)
Function 30, experience 15, salary 15, location 10, company 7, employment type 5, description quality 5, skills 5, freshness 5, direct apply link 3. It only ranks.

## Gemini
Optional. It can only point to a sentence about experience (which must appear verbatim in the description and is then parsed by our code) or suggest a function label. It never changes a rule outcome by itself.
