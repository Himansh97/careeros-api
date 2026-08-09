"""Resume export to PDF and DOCX.

Both formats are built for ATS parsing rather than visual flourish: a single
column, real selectable text, standard section headings, no tables, no text
boxes, no images. Multi-column layouts and graphics are the usual reason a
resume parses into garbage.
"""
from __future__ import annotations

import io
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Inches
from reportlab.lib.enums import TA_CENTER
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
)


def _escape(text: str) -> str:
    """ReportLab paragraphs accept a mini-HTML dialect, so escape markup."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return cleaned or "resume"


def _contact_line(profile: Any) -> str:
    bits = [profile.location, profile.phone, profile.email, profile.linkedin_url]
    return "  |  ".join(b for b in bits if b)


def build_pdf(resume: dict[str, Any], profile: Any) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"{profile.name} — {resume['jobTitle']}",
        author=profile.name,
    )

    base = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "Name", parent=base["Title"], fontSize=17, leading=21, spaceAfter=2,
        alignment=TA_CENTER, fontName="Helvetica-Bold",
    )
    contact_style = ParagraphStyle(
        "Contact", parent=base["Normal"], fontSize=8.5, leading=12,
        alignment=TA_CENTER, textColor="#444444",
    )
    section_style = ParagraphStyle(
        "Section", parent=base["Heading2"], fontSize=10.5, leading=13,
        spaceBefore=11, spaceAfter=3, fontName="Helvetica-Bold",
    )
    role_style = ParagraphStyle(
        "Role", parent=base["Normal"], fontSize=9.8, leading=12.5,
        fontName="Helvetica-Bold", spaceBefore=5,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=base["Normal"], fontSize=8.5, leading=11,
        textColor="#555555", spaceAfter=2,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=base["Normal"], fontSize=9.3, leading=12.6,
    )

    flow: list[Any] = [
        Paragraph(_escape(profile.name), name_style),
        Paragraph(_escape(_contact_line(profile)), contact_style),
        Spacer(1, 7),
        HRFlowable(width="100%", thickness=0.6, color="#BBBBBB", spaceAfter=4),
        Paragraph("EXPERIENCE", section_style),
    ]

    for section in resume.get("sections", []):
        flow.append(Paragraph(_escape(section.get("heading", "")), role_style))
        if section.get("subheading"):
            flow.append(Paragraph(_escape(section["subheading"]), meta_style))
        bullets = [
            ListItem(Paragraph(_escape(b.get("text", "")), bullet_style), leftIndent=12)
            for b in section.get("bullets", [])
        ]
        if bullets:
            flow.append(
                ListFlowable(bullets, bulletType="bullet", start="•", leftIndent=13,
                             bulletFontSize=8, spaceBefore=1)
            )

    education = getattr(profile, "education", []) or []
    if education:
        flow.append(Paragraph("EDUCATION", section_style))
        for e in education:
            line = f"{e.get('degree','')} — {e.get('institution','')}"
            grad = e.get("graduation_date")
            if grad:
                line += f" ({grad})"
            flow.append(Paragraph(_escape(line), bullet_style))

    skills = getattr(profile, "skills_inventory", {}) or {}
    if skills:
        flow.append(Paragraph("SKILLS", section_style))
        for group, items in skills.items():
            label = group.replace("_", " ").title()
            flow.append(
                Paragraph(f"<b>{_escape(label)}:</b> {_escape(', '.join(items))}", bullet_style)
            )

    certs = getattr(profile, "certifications", []) or []
    if certs:
        flow.append(Paragraph("CERTIFICATIONS", section_style))
        flow.append(Paragraph(_escape(", ".join(certs)), bullet_style))

    doc.build(flow)
    return buf.getvalue()


def build_docx(resume: dict[str, Any], profile: Any) -> bytes:
    document = Document()

    for section in document.sections:
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(profile.name)
    run.bold = True
    run.font.size = Pt(16)

    contact = document.add_paragraph()
    contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
    crun = contact.add_run(_contact_line(profile))
    crun.font.size = Pt(9)

    def section_heading(text: str) -> None:
        p = document.add_paragraph()
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(11)

    section_heading("EXPERIENCE")
    for sec in resume.get("sections", []):
        p = document.add_paragraph()
        r = p.add_run(sec.get("heading", ""))
        r.bold = True
        if sec.get("subheading"):
            sp = document.add_paragraph()
            sr = sp.add_run(sec["subheading"])
            sr.italic = True
            sr.font.size = Pt(9)
        for b in sec.get("bullets", []):
            document.add_paragraph(b.get("text", ""), style="List Bullet")

    education = getattr(profile, "education", []) or []
    if education:
        section_heading("EDUCATION")
        for e in education:
            line = f"{e.get('degree','')} — {e.get('institution','')}"
            if e.get("graduation_date"):
                line += f" ({e['graduation_date']})"
            document.add_paragraph(line)

    skills = getattr(profile, "skills_inventory", {}) or {}
    if skills:
        section_heading("SKILLS")
        for group, items in skills.items():
            p = document.add_paragraph()
            r = p.add_run(f"{group.replace('_', ' ').title()}: ")
            r.bold = True
            p.add_run(", ".join(items))

    certs = getattr(profile, "certifications", []) or []
    if certs:
        section_heading("CERTIFICATIONS")
        document.add_paragraph(", ".join(certs))

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
