#!/usr/bin/env python3
"""Build du guide utilisateur RVC en PDF depuis le source Markdown.

Pipeline : ``docs/rvc-user-guide.md`` → HTML stylé → PDF (via WeasyPrint).

Usage :

    # Depuis la racine du repo
    python Site/install/scripts/build_rvc_guide_pdf.py

    # Sortie : Site/backend/assets/rvc-guide.pdf

Dépendances Python (à installer dans le venv backend ou un venv build) :

    pip install markdown weasyprint pygments

WeasyPrint dépend de cairo, pango, gdk-pixbuf (libs système). Sur Ubuntu :

    sudo apt-get install libcairo2 libpango-1.0-0 libpangoft2-1.0-0 \\
                          libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info

Ce script est idempotent : ré-exécutable à volonté, le PDF est ré-écrit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ============================================================================
# Détection des paths
# ============================================================================

# Le script vit dans Site/install/scripts/, le repo root = remonter 3 niveaux
REPO_ROOT = Path(__file__).resolve().parents[3]
MD_SOURCE = REPO_ROOT / "docs" / "rvc-user-guide.md"
PDF_OUTPUT = REPO_ROOT / "Site" / "backend" / "assets" / "rvc-guide.pdf"


# ============================================================================
# Style CSS pour le rendu PDF (page A4, typo claire, blocs code lisibles)
# ============================================================================

CSS = """
@page {
    size: A4;
    margin: 2cm 2cm 2cm 2cm;
    @top-right {
        content: "VoiceBridge V3 — Guide utilisateur";
        font-size: 9pt;
        color: #888;
    }
    @bottom-right {
        content: "Page " counter(page) " / " counter(pages);
        font-size: 9pt;
        color: #888;
    }
    @bottom-left {
        content: "v3.0 · mai 2026";
        font-size: 9pt;
        color: #888;
    }
}

* { box-sizing: border-box; }

body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.55;
    color: #222;
}

h1 {
    font-size: 26pt;
    font-weight: 800;
    color: #6c63ff;
    border-bottom: 2px solid #6c63ff;
    padding-bottom: 0.4em;
    margin-top: 0;
    page-break-before: auto;
}
h2 {
    font-size: 18pt;
    font-weight: 700;
    color: #443f9c;
    margin-top: 1.5em;
    border-bottom: 1px solid #ddd;
    padding-bottom: 0.2em;
    page-break-after: avoid;
}
h3 {
    font-size: 13pt;
    font-weight: 700;
    color: #2a2657;
    margin-top: 1.2em;
    page-break-after: avoid;
}
h4 {
    font-size: 11pt;
    font-weight: 700;
    color: #444;
    margin-top: 1em;
    margin-bottom: 0.3em;
    page-break-after: avoid;
}

p { margin: 0.5em 0; }

a { color: #4943c4; text-decoration: none; }

code {
    font-family: "SF Mono", Menlo, "DejaVu Sans Mono", Consolas, monospace;
    background: #f5f5fa;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 9pt;
    color: #2c2070;
}

pre {
    background: #1e1e2e;
    color: #d8d8e0;
    padding: 0.75em 1em;
    border-radius: 5px;
    font-size: 8.5pt;
    line-height: 1.45;
    overflow-x: auto;
    page-break-inside: avoid;
    white-space: pre-wrap;
    word-wrap: break-word;
}
pre code {
    background: transparent;
    padding: 0;
    color: inherit;
    font-size: inherit;
}

blockquote {
    border-left: 3px solid #6c63ff;
    margin-left: 0;
    padding: 0.4em 1em;
    background: #f5f5fa;
    color: #444;
    font-style: italic;
    border-radius: 0 5px 5px 0;
}
blockquote p { margin: 0.3em 0; }

table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
    font-size: 9pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #ddd;
    padding: 0.4em 0.6em;
    text-align: left;
    vertical-align: top;
}
th {
    background: #6c63ff;
    color: white;
    font-weight: 600;
}
tr:nth-child(even) td {
    background: #f9f9fc;
}

ul, ol { padding-left: 1.4em; margin: 0.4em 0; }
li { margin: 0.15em 0; }

hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 1.5em 0;
}

strong { color: #1a1a3a; }

/* Boîtes warning/info pour les blockquotes contenant ⚠️ ou 💡 */
blockquote:has(strong:first-child:contains("⚠️")) {
    border-left-color: #f5a524;
    background: #fff8e8;
}

/* Sauts de page : avant chaque h2 majeur */
h2 { page-break-before: auto; }
h2:nth-of-type(n+2) { page-break-before: auto; }
"""


# ============================================================================
# Build
# ============================================================================


def build_pdf(md_path: Path, pdf_path: Path) -> Path:
    """Convertit ``md_path`` (Markdown) en ``pdf_path`` (PDF)."""
    try:
        import markdown  # type: ignore
    except ImportError as exc:
        sys.exit(f"❌ Dépendance manquante: pip install markdown ({exc})")

    try:
        from weasyprint import CSS as WeasyCSS, HTML  # type: ignore
    except ImportError as exc:
        sys.exit(f"❌ Dépendance manquante: pip install weasyprint ({exc})")

    if not md_path.exists():
        sys.exit(f"❌ Source Markdown introuvable : {md_path}")

    md_source = md_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_source,
        extensions=[
            "extra",            # tables, footnotes, fenced code blocks, etc.
            "sane_lists",
            "toc",
            "codehilite",
        ],
        extension_configs={
            "codehilite": {"css_class": "codehilite", "guess_lang": False},
        },
    )

    html_full = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>VoiceBridge V3 — Guide utilisateur</title>
</head>
<body>
{html_body}
</body>
</html>
"""

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_full, base_url=str(md_path.parent)).write_pdf(
        target=str(pdf_path),
        stylesheets=[WeasyCSS(string=CSS)],
    )
    print(f"✅ PDF généré : {pdf_path}")
    print(f"   Taille : {pdf_path.stat().st_size / 1024:.1f} Ko")
    return pdf_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", default=str(MD_SOURCE),
        help=f"Markdown source (défaut: {MD_SOURCE})",
    )
    parser.add_argument(
        "--output", default=str(PDF_OUTPUT),
        help=f"Chemin de sortie PDF (défaut: {PDF_OUTPUT})",
    )
    args = parser.parse_args()
    build_pdf(Path(args.source), Path(args.output))


if __name__ == "__main__":
    main()
