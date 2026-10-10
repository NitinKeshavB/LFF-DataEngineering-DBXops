"""
Generate synthetic insurance policy-wording PDFs into a landing UC volume folder.

Each product gets one wording document with fixed, queryable facts (excesses, limits, time limits), so a retrieval
question has exactly one right answer that the evaluation dataset in `src/eval/` can check. The text is built from
static product data, so a run always produces the same documents. Documents already on the volume are left alone,
which keeps Auto Loader from reading a rewritten file twice.

The text and HTML generation is plain Python. Only the HTML -> PDF step needs `plutoprint` (a job-only library,
installed by the job environment), following the databricks-unstructured-pdf-generation skill.

Run as a job task:  generate_policy_docs.py --output /Volumes/<catalog>/<schema>/landing/policy_docs
"""

from __future__ import annotations

import argparse
import html
import importlib
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Callable,
    Sequence,
)

EFFECTIVE_DATE = "1 July 2026"
Converter = Callable[[str, Path], None]


@dataclass(frozen=True)
class PolicyWording:
    """One product's wording: its code, name and the section texts (a section is a title and its paragraphs)."""

    code: str
    name: str
    sections: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)

    @property
    def file_name(self) -> str:
        """Return the PDF file name, `<code>_policy_wording.pdf` in lowercase."""
        return f"{self.code.lower()}_policy_wording.pdf"


def _common_sections(name: str, lodge_days: int, decision_days: int) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the claims, cancellation and complaints sections that every wording shares in shape."""
    return (
        (
            "Making a claim",
            (
                f"Under the {name} policy you must lodge a claim within {lodge_days} days of becoming aware of the "
                f"loss or damage; a later claim may be reduced to the extent that the delay prejudiced the insurer.",
                f"The insurer will give a decision on a lodged {name} claim within {decision_days} business days of "
                f"receiving all the documents it asked for.",
            ),
        ),
        (
            "Cancellation and complaints",
            (
                f"You may cancel the {name} policy within 14 days of the start date and receive a full refund of the "
                f"premium, provided that no claim has been made.",
                f"A complaint about the {name} policy is acknowledged within 1 business day and resolved within 30 "
                f"calendar days, or referred to the external dispute resolution scheme.",
            ),
        ),
    )


def policy_wordings() -> list[PolicyWording]:
    """Return the synthetic wordings, one per product, in a fixed order."""
    return [
        PolicyWording(
            "MTR",
            "Motor Comprehensive",
            (
                (
                    "What is covered",
                    (
                        "The Motor Comprehensive policy covers accidental damage to your vehicle, theft, fire and "
                        "storm damage, and legal liability of up to $20 million for damage to other people's property.",
                        "If your vehicle is stolen and not recovered, the Motor Comprehensive policy pays for a hire "
                        "car for up to 14 days while the claim is assessed.",
                    ),
                ),
                (
                    "Excess",
                    (
                        "The standard excess on the Motor Comprehensive policy is $800 for each claim, and a driver "
                        "under 25 years old adds an additional excess of $1,200.",
                        "A windscreen claim on the Motor Comprehensive policy carries a reduced excess of $100, and "
                        "only one windscreen claim is allowed in each 12 months.",
                    ),
                ),
            )
            + _common_sections("Motor Comprehensive", 30, 10),
        ),
        PolicyWording(
            "HOM",
            "Home Building and Contents",
            (
                (
                    "What is covered",
                    (
                        "The Home Building and Contents policy covers the house, fixed structures and household "
                        "contents against fire, storm, flood, theft and malicious damage.",
                        "Portable valuables such as jewellery are covered by the Home Building and Contents policy "
                        "up to $2,500 for any one item, unless the item is listed on the policy schedule.",
                    ),
                ),
                (
                    "Excess",
                    (
                        "The standard excess on the Home Building and Contents policy is $500 for each claim.",
                        "A claim for storm or flood damage under the Home Building and Contents policy carries an "
                        "excess of $2,000 and has a waiting period of 72 hours after the policy starts.",
                    ),
                ),
            )
            + _common_sections("Home Building and Contents", 60, 15),
        ),
        PolicyWording(
            "TRV",
            "Travel",
            (
                (
                    "What is covered",
                    (
                        "The Travel policy covers unlimited emergency medical expenses overseas, and trip "
                        "cancellation costs of up to $10,000 when you cancel for a covered reason.",
                        "Lost, stolen or damaged luggage is covered by the Travel policy up to $5,000 in total and "
                        "up to $750 for any one item.",
                    ),
                ),
                (
                    "Excess",
                    (
                        "The standard excess on the Travel policy is $250 for each claim, and no excess applies to "
                        "an emergency medical claim for a child under 16 years old.",
                        "A claim for a pre-existing medical condition under the Travel policy is only covered when "
                        "the condition was declared and accepted by the insurer before departure.",
                    ),
                ),
            )
            + _common_sections("Travel", 90, 20),
        ),
        PolicyWording(
            "LLD",
            "Landlord",
            (
                (
                    "What is covered",
                    (
                        "The Landlord policy covers the rental building and landlord contents, and pays loss of rent "
                        "for up to 26 weeks while the property cannot be lived in after an insured event.",
                        "Malicious damage by a tenant is covered by the Landlord policy up to $50,000, and loss of "
                        "rent for tenant default is covered for up to 6 weeks.",
                    ),
                ),
                (
                    "Excess",
                    (
                        "The standard excess on the Landlord policy is $600 for each claim.",
                        "A claim for tenant theft under the Landlord policy carries an excess of $1,000 and needs "
                        "a police report number.",
                    ),
                ),
            )
            + _common_sections("Landlord", 45, 15),
        ),
        PolicyWording(
            "CVN",
            "Caravan and Trailer",
            (
                (
                    "What is covered",
                    (
                        "The Caravan and Trailer policy covers accidental damage, theft and storm damage to the "
                        "caravan or trailer, and its fixed annexe, while it is parked or being towed.",
                        "Contents inside the caravan are covered by the Caravan and Trailer policy up to $3,000, "
                        "and cover for a caravan left unattended applies only when it is locked.",
                    ),
                ),
                (
                    "Excess",
                    (
                        "The standard excess on the Caravan and Trailer policy is $400 for each claim.",
                        "A claim for a caravan stolen from an unlocked site under the Caravan and Trailer policy "
                        "carries an excess of $1,500.",
                    ),
                ),
            )
            + _common_sections("Caravan and Trailer", 30, 10),
        ),
    ]


def render_text(wording: PolicyWording) -> str:
    """Return the wording as plain text: a heading line, then each section title and its paragraphs."""
    lines = [f"{wording.name} Insurance - Policy Wording", f"Effective {EFFECTIVE_DATE}"]
    for title, paragraphs in wording.sections:
        lines.append(title)
        lines.extend(paragraphs)
    return "\n\n".join(lines)


def render_html(wording: PolicyWording) -> str:
    """Return the wording as a simple HTML page; minimal CSS keeps the PDF conversion reliable."""
    body = [f"<h1>{html.escape(wording.name)} Insurance - Policy Wording</h1>"]
    body.append(f"<p>Effective {html.escape(EFFECTIVE_DATE)}</p>")
    for title, paragraphs in wording.sections:
        body.append(f"<h2>{html.escape(title)}</h2>")
        body.extend(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    style = "body { font-family: sans-serif; font-size: 12pt; } h1 { font-size: 20pt; } h2 { font-size: 15pt; }"
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{style}</style></head><body>{''.join(body)}</body></html>"


def plutoprint_converter(page_html: str, pdf_path: Path) -> None:
    """Convert HTML to a PDF file with plutoprint, which only the job environment installs."""
    plutoprint = importlib.import_module("plutoprint")
    book = plutoprint.Book(plutoprint.PAGE_SIZE_A4)
    book.load_html(page_html)
    book.write_to_pdf(str(pdf_path))


def write_documents(output: Path, wordings: Sequence[PolicyWording], convert: Converter) -> list[Path]:
    """Write each wording that is not yet on the volume as a PDF; return the paths written."""
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for wording in wordings:
        path = output / wording.file_name
        if path.exists():
            continue
        convert(render_html(wording), path)
        written.append(path)
    return written


def main(argv: list[str] | None = None, convert: Converter = plutoprint_converter) -> int:
    """Generate the policy wording PDFs; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument(
        "--output", required=True, type=Path, help="landing folder, e.g. /Volumes/<c>/<s>/landing/policy_docs"
    )
    args = parser.parse_args(argv)
    wordings = policy_wordings()
    written = write_documents(args.output, wordings, convert)
    print(f"wrote {len(written)} of {len(wordings)} policy wording PDFs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
