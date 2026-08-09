"""Read every rewrite as a sentence, not as a regex match."""
from app.phrasing import align_bullet, EQUIVALENCES

CASES = [
    ("Built and deployed a production data pipeline using Python, SQL, and PySpark.",
     "We need ETL pipeline experience."),
    ("Used statistical modeling to build a standardized data-cleansing pipeline across three systems.",
     "Strong ETL pipeline skills required."),
    ("Consolidated data into a unified SQL-to-Power BI reporting infrastructure, reducing cycle time 40%.",
     "You will build dashboards for stakeholders."),
    ("Conducted statistical root-cause analysis on recurring delivery delays.",
     "Root cause analysis is core to this role."),
    ("Gathered requirements from accounting stakeholders and built a solution.",
     "Requirements elicitation with business partners."),
    ("Developed an AI-driven data-extraction model using the Claude API, validating output against a MISMO schema.",
     "Data validation and quality checks required."),
]

BAD = [
    # ETL must keep a head noun after it; "a production ETL" is wrong,
    # "a production ETL pipeline" is right.
    (r"\bETL(?!\s+(pipeline|process|workflow))\b(?!\s*[,.])", "bare acronym used as a noun"),
    (r"\b(a|an)\s+(?:[\w-]+\s+){0,3}(dashboards|pipelines|models)\b", "article/plural mismatch"),
    (r"\b(\w+)\s+\1\b", "duplicated word"),
    (r",\s*(data validation|analysis)\s+against", "verb phrase replaced by noun"),
]

import re
fails = 0
for text, jd in CASES:
    out, changes = align_bullet(text, jd)
    print(("CHANGED " if changes else "kept    ") + out)
    for pattern, why in BAD:
        if re.search(pattern, out, re.I):
            print(f"    FAIL — {why}")
            fails += 1
print(f"\nequivalences active: {len(EQUIVALENCES)} | failures: {fails}")
raise SystemExit(1 if fails else 0)
