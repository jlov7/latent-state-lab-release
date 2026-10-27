"""Validate the curated public documentation and self-contained workflow SVG."""
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    "README.md", "CONTRIBUTING.md", "SECURITY.md",
    *("docs/" + name + ".md" for name in (
        "USAGE", "DESIGN", "EVALUATION", "LIMITATIONS", "REFERENCES",
        "STABILITY", "RELEASING",
    )),
)


def anchors(text):
    seen = Counter()
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        suffix = f"-{seen[slug]}" if seen[slug] else ""
        seen[slug] += 1
        result.add(slug + suffix)
    return result


def validate(root):
    links = 0
    for name in PUBLIC_DOCS:
        source = root / name
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\s]+)\)", text):
            ref = urlsplit(target.strip("<>"))
            if ref.scheme or ref.netloc:
                continue
            path = (source.parent / unquote(ref.path)).resolve() if ref.path else source
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"missing or escaping local link in {name}: {target}")
            if (ref.fragment and path.suffix == ".md" and
                    unquote(ref.fragment) not in anchors(path.read_text(encoding="utf-8"))):
                raise ValueError(f"missing local anchor in {name}: {target}")
            links += 1
    image = root / "docs/images/workflow.svg"
    svg = ET.fromstring(image.read_text(encoding="utf-8"))
    ns = "{http://www.w3.org/2000/svg}"
    if svg.tag != ns + "svg" or not svg.findtext(ns + "title") or not svg.findtext(ns + "desc"):
        raise ValueError("workflow SVG needs its accessible title and description")
    if svg.get("viewBox") != "0 0 1080 530":
        raise ValueError("workflow SVG viewBox changed; inspect its legibility")
    for node in svg.iter():
        if node.tag.rsplit("}", 1)[-1] in ("script", "foreignObject"):
            raise ValueError("workflow SVG must be a static self-contained diagram")
        for key, value in node.attrib.items():
            if key.lower().startswith("on") or ("href" in key and not value.startswith("#")):
                raise ValueError("workflow SVG must not execute code or load external content")
    return {"status": "pass", "documents": len(PUBLIC_DOCS), "local_links": links,
            "image": "docs/images/workflow.svg"}


if __name__ == "__main__":
    print(json.dumps(validate(ROOT)))
