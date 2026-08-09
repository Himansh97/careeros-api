"""Canonical skill vocabulary used to read requirements out of a job description.

This deliberately covers far more than the candidate knows. If the vocabulary
were built only from the candidate's own skills, every job would score as a
perfect match and true gaps would be invisible — the exact dishonesty this
system exists to prevent.

Each entry maps a canonical skill name to the surface forms that indicate it.
"""
from __future__ import annotations

SKILL_ALIASES: dict[str, list[str]] = {
    # Languages
    "Python": ["python"],
    "R": [" r ", " r,", "(r)", " r/"],
    "SQL": ["sql"],
    "Scala": ["scala"],
    "Java": ["java "],
    "JavaScript": ["javascript", "typescript"],
    "Go": ["golang"],
    "SAS": ["sas"],
    "C++": ["c++"],
    # Data engineering
    "PySpark": ["pyspark"],
    "Spark": ["apache spark", "spark"],
    "Airflow": ["airflow"],
    "Hadoop": ["hadoop"],
    "Hive": ["hive"],
    "Kafka": ["kafka"],
    "dbt": ["dbt"],
    "ETL": ["etl", "elt"],
    "Data pipelines": ["data pipeline", "data pipelines", "pipelines"],
    "Data modeling": ["data modeling", "data modelling", "dimensional model"],
    "Data quality": ["data quality", "data integrity", "data validation"],
    "Data warehousing": ["data warehouse", "warehousing"],
    # Cloud / infra
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "CI/CD": ["ci/cd", "continuous integration", "continuous delivery"],
    "Terraform": ["terraform"],
    # Databases / warehouses
    "Snowflake": ["snowflake"],
    "Databricks": ["databricks"],
    "Redshift": ["redshift"],
    "BigQuery": ["bigquery"],
    "Postgres": ["postgres", "postgresql"],
    # BI / viz
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Looker": ["looker"],
    "Excel": ["excel"],
    # ML / stats
    "Machine learning": ["machine learning", " ml ", "ml models"],
    "Statistical modeling": ["statistical model", "statistics", "statistical analysis"],
    "Regression analysis": ["regression"],
    "Hypothesis testing": ["hypothesis test", "a/b test", "ab testing", "experimentation"],
    "Forecasting": ["forecasting", "forecast"],
    "Feature engineering": ["feature engineering"],
    "TensorFlow": ["tensorflow"],
    "PyTorch": ["pytorch"],
    "LLM": ["llm", "large language model", "generative ai", "genai"],
    "NLP": ["nlp", "natural language processing"],
    # Product / delivery
    "Agile": ["agile", "scrum"],
    "Project management": ["project management", "program management"],
    "Requirements gathering": [
        "requirements gathering",
        "gather requirements",
        "business requirements",
        "elicit",
    ],
    "Stakeholder management": ["stakeholder"],
    "Cross-functional collaboration": ["cross-functional", "cross functional"],
    "Dashboarding": ["dashboard"],
    "Reporting": ["reporting", "reports"],
    "Process improvement": ["process improvement", "process design", "workflow"],
    "Mentoring": ["mentor", "coaching"],
    # Domain
    "Financial services": ["financial services", "fintech", "banking"],
    "Healthcare": ["healthcare", "hipaa"],
    "Pharmaceutical": ["pharmaceutical", "pharma"],
    "Salesforce": ["salesforce"],
    "Jira": ["jira"],
}

# Requirements that carry more weight when present in a JD.
CORE_SKILLS = {
    "Python",
    "SQL",
    "Data pipelines",
    "ETL",
    "Machine learning",
    "Statistical modeling",
    "Requirements gathering",
    "Tableau",
    "Power BI",
    "Data modeling",
}


def extract_requirements(description: str) -> list[tuple[str, bool]]:
    """Return (skill, is_required) pairs found in a job description.

    `is_required` is a heuristic: a skill mentioned in a sentence containing
    "required"/"must have", or that appears more than twice, is treated as
    required; otherwise preferred.
    """
    text = f" {description.lower()} "
    found: list[tuple[str, bool]] = []

    for skill, aliases in SKILL_ALIASES.items():
        occurrences = 0
        matched = False
        for alias in aliases:
            count = text.count(alias)
            if count:
                matched = True
                occurrences += count
        if not matched:
            continue

        required = occurrences >= 3 or skill in CORE_SKILLS
        # Look for explicit requirement language near the first mention.
        for alias in aliases:
            idx = text.find(alias)
            if idx == -1:
                continue
            window = text[max(0, idx - 220) : idx + 220]
            if any(
                phrase in window
                for phrase in ("required", "must have", "you have", "minimum")
            ):
                required = True
                break
        found.append((skill, required))

    return found
