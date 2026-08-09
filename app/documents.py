"""Resume export to PDF and DOCX.

The layout mirrors the candidate's own LaTeX base resume: a centered header
with headline and credentials, a professional summary, categorised skills,
then experience with right-aligned dates and an italic role subtitle, and
education with right-aligned date and GPA.

It stays ATS-safe throughout: single column, real selectable text, standard
section headings, no text boxes or images. Right-aligned dates use a
borderless two-cell row (PDF) and a tab stop (DOCX) rather than a layout
table, so parsers still read one linear column of text.
"""
from __future__ import annotations

import io
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DARK = colors.HexColor("#282828")


def _escape(text: str) -> str:
    """ReportLab paragraphs accept a mini-HTML dialect, so escape markup."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pretty_month(token: str) -> str:
    from .tailor import _pretty_date

    return _pretty_date(token)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return cleaned or "resume"


def _contact_line(profile: Any) -> str:
    linkedin = (
        (profile.linkedin_url or "")
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .rstrip("/")
    )
    return "  |  ".join(b for b in [profile.phone, profile.email, linkedin, profile.location] if b)


def _edu_period(edu: dict[str, Any]) -> str:
    """Show the study period, not just graduation.

    A lone graduation date leaves the employment gap the degree explains
    looking like an unexplained absence.
    """
    period = _pretty_month(edu.get("graduation_date", ""))
    if edu.get("gpa"):
        period += f", GPA {edu['gpa']}"
    return period


def _role_meta(profile: Any, employer: str) -> dict[str, Any]:
    for role in profile.employment_history or []:
        if role.get("employer") == employer:
            return role
    return {}


# Title-casing mangles acronyms ("Bi & Visualization", "Software & Devops"),
# so the labels that matter are spelled out to match the base resume exactly.
GROUP_LABELS = {
    "business_analytics_and_statistical_modeling": "Business Analytics & Statistical Modeling",
    "data_engineering_and_big_data": "Data Engineering & Big Data",
    "bi_and_visualization": "BI & Visualization",
    "project_and_delivery_management": "Project & Delivery Management",
    "software_and_devops": "Software & DevOps",
    "compliance": "Compliance",
}

ACRONYMS = {"Bi": "BI", "Devops": "DevOps", "Ai": "AI", "Ml": "ML", "Sql": "SQL",
            "Etl": "ETL", "Api": "API", "Ui": "UI", "Ux": "UX"}


def _group_label(group: str) -> str:
    if group in GROUP_LABELS:
        return GROUP_LABELS[group]
    label = group.replace("_", " ").replace(" and ", " & ").title()
    for wrong, right in ACRONYMS.items():
        label = re.sub(rf"\b{wrong}\b", right, label)
    return label


def _prioritised_skills(resume: dict[str, Any], profile: Any) -> list[tuple[str, list[str]]]:
    """Order skill groups, and skills within them, by what the job asked for.

    The content is unchanged — every skill the candidate has is still listed.
    Only the ordering adapts, so a recruiter scanning the first line sees the
    skills their posting actually named instead of a fixed house order.
    """
    wanted = {w.lower() for w in (resume.get("matchedSkills") or [])}
    groups = list((getattr(profile, "skills_inventory", {}) or {}).items())

    def rank_item(item: str) -> int:
        low = item.lower()
        return 0 if any(w == low or w in low for w in wanted) else 1

    ordered: list[tuple[str, list[str], int]] = []
    for name, items in groups:
        hits = sum(1 for i in items if rank_item(i) == 0)
        ordered.append((name, sorted(items, key=rank_item), hits))

    ordered.sort(key=lambda g: -g[2])
    return [(name, items) for name, items, _ in ordered]


def _page_count(pdf_bytes: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def _trim(resume: dict[str, Any], lead: int, rest: int) -> dict[str, Any]:
    """A copy of the resume with bullets capped per role.

    Bullets arrive relevance-ordered, so trimming drops the least relevant
    evidence first. A job matching more of the candidate's background keeps
    more bullets — the page fills itself according to the role.
    """
    trimmed = dict(resume)
    trimmed["sections"] = [
        {**s, "bullets": s["bullets"][: lead if i == 0 else rest]}
        for i, s in enumerate(resume.get("sections", []))
    ]
    return trimmed


# Typographic density levels, loosest first. Tightening leading and font size
# reclaims far more space than cutting bullets does, and costs nothing —
# whereas every dropped bullet is real evidence the employer no longer sees.
DENSITIES = [
    {"body": 9.4, "lead": 12.6, "gap": 10, "margin": 0.62},
    {"body": 9.1, "lead": 11.9, "gap": 9, "margin": 0.58},
    {"body": 8.8, "lead": 11.2, "gap": 8, "margin": 0.55},
    {"body": 8.5, "lead": 10.6, "gap": 7, "margin": 0.5},
    {"body": 8.2, "lead": 10.1, "gap": 6, "margin": 0.45},
]


def build_pdf(resume: dict[str, Any], profile: Any) -> bytes:
    """Render, measure, and re-render until the content fills one page.

    Order matters: compress whitespace first, and only start dropping
    bullets once the tightest readable typography still overflows. A page
    that spills three lines onto a second sheet reads worse than either a
    full single page or a genuinely full two.
    """
    # Pass 1 — keep every relevant bullet, tighten the layout.
    last: bytes | None = None
    for density in DENSITIES:
        candidate = _render_pdf(resume, profile, density)
        if _page_count(candidate) == 1:
            return candidate
        last = candidate

    # Pass 2 — still overflowing, so trim least-relevant bullets at the
    # tightest density.
    tight = DENSITIES[-1]
    for lead, rest in [(5, 3), (5, 2), (4, 2), (4, 1), (3, 1)]:
        candidate = _render_pdf(_trim(resume, lead, rest), profile, tight)
        if _page_count(candidate) == 1:
            return candidate
        last = candidate

    return last or _render_pdf(resume, profile, tight)


# --------------------------------------------------------------------- PDF
def _render_pdf(resume: dict[str, Any], profile: Any,
                density: dict[str, float] | None = None) -> bytes:
    d = density or DENSITIES[1]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=d["margin"] * inch,
        rightMargin=d["margin"] * inch,
        topMargin=d["margin"] * inch,
        bottomMargin=(d["margin"] - 0.1) * inch,
        title=f"{profile.name} - {resume['jobTitle']}",
        author=profile.name,
    )
    width = doc.width
    base = getSampleStyleSheet()

    name_s = ParagraphStyle("Name", parent=base["Title"], fontSize=16.5, leading=19,
                            alignment=TA_CENTER, spaceAfter=1, textColor=DARK,
                            fontName="Helvetica-Bold")
    headline_s = ParagraphStyle("Headline", parent=base["Normal"], fontSize=10, leading=13,
                                alignment=TA_CENTER, fontName="Helvetica-Oblique",
                                textColor=DARK)
    contact_s = ParagraphStyle("Contact", parent=base["Normal"], fontSize=8.8, leading=12,
                               alignment=TA_CENTER)
    creds_s = ParagraphStyle("Creds", parent=base["Normal"], fontSize=8.8, leading=12,
                             alignment=TA_CENTER, fontName="Helvetica-Oblique")
    section_s = ParagraphStyle("Section", parent=base["Heading2"],
                               fontSize=d["body"] + 1.3, leading=d["lead"],
                               spaceBefore=d["gap"], spaceAfter=1, textColor=DARK,
                               fontName="Helvetica-Bold")
    body_s = ParagraphStyle("Body", parent=base["Normal"], fontSize=d["body"],
                            leading=d["lead"], alignment=TA_JUSTIFY)
    sub_s = ParagraphStyle("Sub", parent=base["Normal"], fontSize=d["body"] - 0.3,
                           leading=d["lead"] - 0.8,
                           fontName="Helvetica-Oblique",
                           textColor=colors.HexColor("#444444"))

    def rule() -> HRFlowable:
        return HRFlowable(width="100%", thickness=0.7, color=DARK, spaceBefore=1, spaceAfter=4)

    def split_row(left: str, right: str, left_ratio: float = 0.74) -> Table:
        """Left text with a right-aligned counterpart, as LaTeX \\hfill does."""
        right_s = ParagraphStyle("R", parent=body_s, alignment=2)
        t = Table([[Paragraph(left, body_s), Paragraph(right, right_s)]],
                  colWidths=[width * left_ratio, width * (1 - left_ratio)])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        return t

    flow: list[Any] = [Paragraph(_escape(profile.name.upper()), name_s)]
    headline = resume.get("headline") or getattr(profile, "headline", "")
    if headline:
        flow.append(Paragraph(_escape(headline), headline_s))
    flow.append(Paragraph(_escape(_contact_line(profile)), contact_s))
    if getattr(profile, "credentials_line", []):
        flow.append(Paragraph(_escape("  |  ".join(profile.credentials_line)), creds_s))
    flow.append(Spacer(1, 3))

    summary = resume.get("summary") or getattr(profile, "professional_summary", "")
    if summary:
        flow += [Paragraph("PROFESSIONAL SUMMARY", section_s), rule(),
                 Paragraph(_escape(summary), body_s)]

    skills = _prioritised_skills(resume, profile)
    if skills:
        flow += [Paragraph("SKILLS", section_s), rule()]
        for group, items in skills:
            flow.append(Paragraph(
                f"<b>{_escape(_group_label(group))}:</b> {_escape(', '.join(items))}", body_s))
        certs = getattr(profile, "certifications", []) or []
        if certs:
            flow.append(Paragraph(
                f"<b>Certifications:</b> {_escape(', '.join(certs))}", body_s))

    flow += [Paragraph("PROFESSIONAL EXPERIENCE", section_s), rule()]
    for section in resume.get("sections", []):
        meta = _role_meta(profile, section.get("employer", ""))
        dates = section.get("subheading", "").split(" · ")[0]
        location = meta.get("location", "")
        left = f"<b>{_escape(section.get('heading',''))}"
        if location:
            left += f", {_escape(location)}"
        left += "</b>"
        flow.append(split_row(left, _escape(dates)))
        if meta.get("subtitle"):
            flow.append(Paragraph(_escape(meta["subtitle"]), sub_s))
        if meta.get("tools"):
            flow.append(Paragraph(_escape(meta["tools"]), sub_s))
        bullets = [ListItem(Paragraph(_escape(b.get("text", "")), body_s), leftIndent=11)
                   for b in section.get("bullets", [])]
        if bullets:
            flow.append(ListFlowable(bullets, bulletType="bullet", start="•",
                                     leftIndent=12, bulletFontSize=7, spaceBefore=1))
        flow.append(Spacer(1, 3))

    education = getattr(profile, "education", []) or []
    if education:
        flow += [Paragraph("EDUCATION", section_s), rule()]
        for e in education:
            left = f"<b>{_escape(e.get('degree',''))}</b> - {_escape(e.get('institution',''))}"
            if e.get("location"):
                left += f", {_escape(e['location'])}"
            right = _edu_period(e)
            flow.append(split_row(left, f"<b>{_escape(right)}</b>", left_ratio=0.80))

    doc.build(flow)
    return buf.getvalue()


# -------------------------------------------------------------------- DOCX
def build_docx(resume: dict[str, Any], profile: Any) -> bytes:
    document = Document()
    for s in document.sections:
        s.left_margin = s.right_margin = Inches(0.6)
        s.top_margin = Inches(0.55)
        s.bottom_margin = Inches(0.5)

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(2)

    def centered(text: str, size: float, bold: bool = False, italic: bool = False) -> None:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        r.font.size = Pt(size)

    centered(profile.name.upper(), 16, bold=True)
    headline = resume.get("headline") or getattr(profile, "headline", "")
    if headline:
        centered(headline, 10.5, italic=True)
    centered(_contact_line(profile), 9)
    if getattr(profile, "credentials_line", []):
        centered("  |  ".join(profile.credentials_line), 9, italic=True)

    def section_heading(text: str) -> None:
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(11)
        r.font.color.rgb = RGBColor(0x28, 0x28, 0x28)

    def split_row(left: str, right: str) -> None:
        """Right-align the date with a tab stop rather than a layout table."""
        p = document.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(Inches(7.2), WD_TAB_ALIGNMENT.RIGHT)
        r = p.add_run(left)
        r.bold = True
        rr = p.add_run("\t" + right)
        rr.bold = True

    summary = resume.get("summary") or getattr(profile, "professional_summary", "")
    if summary:
        section_heading("PROFESSIONAL SUMMARY")
        document.add_paragraph(summary)

    skills = _prioritised_skills(resume, profile)
    if skills:
        section_heading("SKILLS")
        for group, items in skills:
            p = document.add_paragraph()
            r = p.add_run(f"{_group_label(group)}: ")
            r.bold = True
            p.add_run(", ".join(items))
        certs = getattr(profile, "certifications", []) or []
        if certs:
            p = document.add_paragraph()
            r = p.add_run("Certifications: ")
            r.bold = True
            p.add_run(", ".join(certs))

    section_heading("PROFESSIONAL EXPERIENCE")
    for section in resume.get("sections", []):
        meta = _role_meta(profile, section.get("employer", ""))
        heading = section.get("heading", "")
        if meta.get("location"):
            heading += f", {meta['location']}"
        split_row(heading, section.get("subheading", "").split(" · ")[0])
        for line in (meta.get("subtitle"), meta.get("tools")):
            if not line:
                continue
            p = document.add_paragraph()
            r = p.add_run(line)
            r.italic = True
            r.font.size = Pt(9)
        for b in section.get("bullets", []):
            document.add_paragraph(b.get("text", ""), style="List Bullet")

    education = getattr(profile, "education", []) or []
    if education:
        section_heading("EDUCATION")
        for e in education:
            left = f"{e.get('degree','')} - {e.get('institution','')}"
            if e.get("location"):
                left += f", {e['location']}"
            split_row(left, _edu_period(e))

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
