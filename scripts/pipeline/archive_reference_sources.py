"""Archive explicitly selected official references; never access occurrence APIs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "tr", "li", "br", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def official_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host.endswith(".ro.gov.br") or host.endswith(".ro.leg.br")
        or host.endswith(".tjro.jus.br") or host == "portal.trt14.jus.br"
        or host in ("www.diariomunicipal.com.br", "diariomunicipal.com.br"))


def archive_one(target, folder):
    url = target["url"]
    source_id = hashlib.sha256(url.encode()).hexdigest()[:20]
    result = {**target, "source_id": source_id, "retrieved_at": datetime.now(timezone.utc).isoformat()}
    try:
        current = url
        for _ in range(6):
            if not official_url(current):
                raise ValueError("unreviewed_source_domain")
            response = requests.get(current, timeout=(10, 30), allow_redirects=False)
            if response.is_redirect:
                current = urljoin(current, response.headers["Location"])
                continue
            response.raise_for_status()
            break
        else:
            raise ValueError("redirect_limit")
        payload = response.content
        if len(payload) > 25_000_000:
            raise ValueError("reference_too_large")
        # Some official download handlers prepend a PHP notice to an actual PDF.
        extension = ".pdf" if b"%PDF" in payload[:1024] else ".html"
        path = folder / (source_id + extension)
        with path.open("xb") as stream:
            stream.write(payload)
        result.update({"status": "archived", "final_url": current, "path": path.name,
                       "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)})
        if extension == ".html":
            response.encoding = response.apparent_encoding
            parser = PlainText()
            parser.feed(response.text)
            text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
            (folder / (source_id + ".txt")).write_text(text, encoding="utf8")
            if current.rstrip("/").endswith("/portal") and current != url:
                result["status"] = "redirected_to_current_portal_not_historical_evidence"
    except (requests.RequestException, ValueError, OSError) as error:
        result.update({"status": "unavailable", "error_type": type(error).__name__})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    targets = json.loads(args.targets.read_text(encoding="utf8"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda target: archive_one(target, args.output), targets))
    (args.output / "acquisition.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"targets": len(results), "archived": sum(r["status"] == "archived" for r in results),
                      "unavailable_or_redirected": sum(r["status"] != "archived" for r in results)}))


if __name__ == "__main__":
    main()
