#!/usr/bin/env python3
"""Render a story page from a JSON file to stdout, for a dry-run review.

No database, no network, no app import. The JSON file holds:

    {
      "slug": "jane-doe",
      "name_line": "Jane Doe, Teacher",
      "ref_slug": "",                       optional
      "content": {
        "story": "Paragraph one.\\n\\nParagraph two.",
        "links": [{"label": "Read about X", "path": "/begin/product/x"}],
        "photo": {"src": "/static/...", "alt": "..."}   optional
      }
    }

The content is checked with the same rules the console uses, so a file that renders
here will also be accepted by story_page.create.

Usage:
    python3 scripts/story_dry_run.py page.json > page.html
    python3 scripts/story_dry_run.py page.json --preview > preview.html
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import story_pages as _sp  # noqa: E402
from dashboard import story_render as _sr  # noqa: E402


def build_page(data):
    slug = (data.get("slug") or "").strip()
    if not _sp.valid_slug(slug):
        raise _sp.StoryError("bad_slug", slug)
    name_line = (data.get("name_line") or "").strip()
    if not name_line:
        raise _sp.StoryError("bad_name_line", "a name line is required")
    ref_slug = (data.get("ref_slug") or "").strip()
    if ref_slug and not _sp.valid_ref_slug(ref_slug):
        raise _sp.StoryError("bad_ref_slug", ref_slug)
    content = _sp.normalize_content(data.get("content"))
    return {
        "story_slug": slug, "slug": slug, "name_line": name_line, "ref_slug": ref_slug,
        "content": content, "state": "draft", "testimonial_id": data.get("testimonial_id", 0),
        "content_hash": "",
        "current_hash": _sp.compute_hash(content, name_line, ref_slug,
                                         data.get("testimonial_id", 0)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("json_file")
    ap.add_argument("--preview", action="store_true",
                    help="add the console's not-published banner and hash")
    ap.add_argument("--ref-approved", action="store_true",
                    help="treat ref_slug as an approved affiliate, so links carry ?ref=")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    try:
        page = build_page(data)
    except _sp.StoryError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    html = (_sr.render_preview_html(page, testimonial=None, ref_approved=args.ref_approved)
            if args.preview else _sr.render_page_html(page, ref_approved=args.ref_approved))
    sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
