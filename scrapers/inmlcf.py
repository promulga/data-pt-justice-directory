#!/usr/bin/env python3
"""Scraper for INMLCF (Instituto Nacional de Medicina Legal e Ciencias Forenses).

Harvests the Sede, the 3 delegacoes (Norte/Centro/Sul) and every gabinete
medico-legal e forense (including extensoes) from the official site
https://inmlcf.justica.gov.pt/Contactos and writes data/inmlcf.csv|json
using the common directory schema.

Stdlib only.
"""

import csv
import html
import json
import os
import re
import sys
import time
import urllib.request

BASE = "https://inmlcf.justica.gov.pt"
CONTACTS_URL = BASE + "/Contactos"
CATEGORY = "medicina_legal"
DELAY = 0.4
TIMEOUT = 40
RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9",
}

FIELDS = [
    "name", "category", "address", "postal_code", "locality",
    "district_or_region", "phone", "fax", "email", "website",
    "latitude", "longitude", "source_url",
]

# locality -> district (or autonomous region) mapping
DISTRICT = {
    "Abrantes": "Santarém",
    "Angra do Heroísmo": "Açores",
    "Aveiro": "Aveiro",
    "Beja": "Beja",
    "Braga": "Braga",
    "Bragança": "Bragança",
    "Cascais": "Lisboa",
    "Castelo Branco": "Castelo Branco",
    "Chaves": "Vila Real",
    "Coimbra": "Coimbra",
    "Covilhã": "Castelo Branco",
    "Elvas": "Portalegre",
    "Évora": "Évora",
    "Faro": "Faro",
    "Funchal": "Madeira",
    "Guarda": "Guarda",
    "Guimarães": "Braga",
    "Leiria": "Leiria",
    "Lisboa": "Lisboa",
    "Mirandela": "Bragança",
    "Penafiel": "Porto",
    "Ponta Delgada": "Açores",
    "Portalegre": "Portalegre",
    "Portimão": "Faro",
    "Porto": "Porto",
    "Santa Maria da Feira": "Aveiro",
    "Santarém": "Santarém",
    "Santiago do Cacém": "Setúbal",
    "Setúbal": "Setúbal",
    "Tomar": "Santarém",
    "Torres Vedras": "Lisboa",
    "Viana do Castelo": "Viana do Castelo",
    "Vila Franca de Xira": "Lisboa",
    "Vila Real": "Vila Real",
    "Viseu": "Viseu",
}


def fetch(url):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace"), resp.geturl()
        except Exception as exc:  # transient 5xx / TLS hiccups
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last))


def clean(text):
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def strip_tags(fragment):
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</p\s*>", "\n", fragment)
    fragment = re.sub(r"(?i)</li\s*>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    lines = [clean(l) for l in fragment.split("\n")]
    return [l for l in lines if l]


def section(page, heading):
    """Return the text lines of the right_servicos block following <h4>heading</h4>."""
    m = re.search(
        r"<h4>\s*%s\s*</h4>.*?<div class=\"right_servicos[^\"]*\">(.*?)</div>"
        % re.escape(heading),
        page, re.S | re.I)
    if not m:
        return []
    return strip_tags(m.group(1))


def page_title(page):
    names = re.findall(r'itemprop="name">([^<]+)</span>', page)
    return clean(names[-1]) if names else ""


def display_name(raw):
    t = raw
    t = re.sub(r"^Contacto\s+(da|do|de)\s+", "", t)
    t = re.sub(r"^-?\s*Contacto\s+", "", t)
    t = t.strip(" -")
    if t.lower().startswith("sede"):
        return "INMLCF - Sede"
    if t.lower().startswith("delega"):
        return "INMLCF - " + t
    return "Gabinete Médico-Legal e Forense - " + t


def parse_phone_fax(lines):
    phone = fax = ""
    for line in lines:
        m = re.search(r"(?i)tel\.?[^0-9+]*(?:\(\+?351\))?\s*([\d ]{7,})", line)
        if m and not phone:
            phone = "+351 " + clean(m.group(1))
        m = re.search(r"(?i)fax[^0-9+]*(?:\(\+?351\))?\s*([\d ]{7,})", line)
        if m and not fax:
            fax = "+351 " + clean(m.group(1))
    return phone, fax


def parse_address(lines):
    address_parts, postal, locality = [], "", ""
    for line in lines:
        m = re.search(r"(\d{4}-\d{3})\s*(.*)$", line)
        if m and not postal:
            postal = m.group(1)
            locality = clean(m.group(2))
            before = clean(line[:m.start()])
            if before:
                address_parts.append(before)
        else:
            address_parts.append(line)
    return ", ".join(address_parts), postal, locality


def fix_locality(locality, raw_title):
    """Normalize source quirks: all-caps localities and '6200-251 Portugal'."""
    if locality.isupper():
        locality = locality.title()
    if locality in ("", "Portugal"):
        m = re.search(r"\(([^)]+)\)", raw_title)
        if m:
            locality = clean(re.sub(r"(?i)Extensão( de)?", "", m.group(1)))
    return locality


def parse_coords(page):
    m = re.search(r"google\.com/maps/embed[^\"]*!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)", page)
    if not m:
        return "", ""
    lon, lat = m.group(1), m.group(2)
    return lat, lon


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(root, "data")
    os.makedirs(outdir, exist_ok=True)

    index, _ = fetch(CONTACTS_URL)
    time.sleep(DELAY)

    links = {}
    for href in re.findall(r'href="([^"]+)"', index):
        path = href.replace(BASE, "")
        if not path.startswith("/"):
            continue
        slug = path.rsplit("/", 1)[-1]
        if not re.match(r"^-?Contacto-", slug):
            continue
        links.setdefault(slug, path)
    links = sorted(links.items())
    print("detail pages found: %d" % len(links), file=sys.stderr)

    rows = []
    for slug, path in links:
        url = BASE + path
        page, final_url = fetch(url)
        time.sleep(DELAY)

        raw_title = page_title(page)
        name = display_name(raw_title)
        phone, fax = parse_phone_fax(
            section(page, "Telefone/fax") or section(page, "Telefone"))
        address, postal, locality = parse_address(section(page, "Morada"))
        locality = fix_locality(locality, raw_title)
        lat, lon = parse_coords(page)
        district = DISTRICT.get(locality, "")
        if not district and locality:
            print("WARN: no district for locality %r (%s)" % (locality, slug),
                  file=sys.stderr)

        rows.append({
            "name": name,
            "category": CATEGORY,
            "address": address,
            "postal_code": postal,
            "locality": locality,
            "district_or_region": district,
            "phone": phone,
            "fax": fax,
            "email": "",  # site only offers contact forms, no direct addresses
            "website": BASE,
            "latitude": lat,
            "longitude": lon,
            "source_url": final_url,
        })
        print("  %-60s %s %s" % (name[:60], postal, locality), file=sys.stderr)

    rows.sort(key=lambda r: (r["district_or_region"], r["locality"], r["name"]))

    csv_path = os.path.join(outdir, "inmlcf.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    json_path = os.path.join(outdir, "inmlcf.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("wrote %d rows -> %s, %s" % (len(rows), csv_path, json_path),
          file=sys.stderr)


if __name__ == "__main__":
    main()
