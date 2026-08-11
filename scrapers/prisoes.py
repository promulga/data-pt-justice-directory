#!/usr/bin/env python3
"""Scrape the DGRSP directory of Estabelecimentos Prisionais (prisons) and
Centros Educativos (juvenile detention/education centres).

Source: https://dgrsp.justica.gov.pt (Direcao-Geral de Reinsercao e Servicos
Prisionais) - the official authority that runs every prison and educational
centre in Portugal.

Stdlib only. Outputs data/prisoes.csv and data/prisoes.json with columns:
name, category, address, postal_code, locality, district_or_region,
phone, fax, email, website, latitude, longitude, source_url
"""

import csv
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://dgrsp.justica.gov.pt"
EP_LIST_URL = (
    BASE
    + "/Justi%C3%A7a-de-adultos/Penas-e-medidas-privativas-de-liberdade"
    + "/Estabelecimentos-prisionais"
)
CE_LIST_URL = BASE + "/Justi%C3%A7a-juvenil/Medidas-institucionais/Centros-educativos"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

FIELDS = [
    "name", "category", "address", "postal_code", "locality",
    "district_or_region", "phone", "fax", "email", "website",
    "latitude", "longitude", "source_url",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "pt-PT,pt;q=0.9",
}

# Locality (as printed after the postal code) -> district / region.
DISTRICT_BY_LOCALITY = {
    "alcoentre": "Lisboa",
    "angra do heroismo": "Açores",
    "aveiro": "Aveiro",
    "beja": "Beja",
    "belas": "Lisboa",
    "braga": "Braga",
    "braganca": "Bragança",
    "caldas da rainha": "Leiria",
    "carregueira": "Lisboa",
    "castelo branco": "Castelo Branco",
    "caxias": "Lisboa",
    "chaves": "Vila Real",
    "coimbra": "Coimbra",
    "covilha": "Castelo Branco",
    "elvas": "Portalegre",
    "evora": "Évora",
    "faro": "Faro",
    "funchal": "Madeira",
    "grandola": "Setúbal",
    "guarda": "Guarda",
    "guimaraes": "Braga",
    "horta": "Açores",
    "izeda": "Bragança",
    "lamego": "Viseu",
    "leiria": "Leiria",
    "linho": "Lisboa",
    "lisboa": "Lisboa",
    "matosinhos": "Porto",
    "monsanto": "Lisboa",
    "montijo": "Setúbal",
    "odemira": "Beja",
    "olhao": "Faro",
    "pacos de ferreira": "Porto",
    "pinheiro da cruz": "Setúbal",
    "ponta delgada": "Açores",
    "portalegre": "Portalegre",
    "porto": "Porto",
    "santarem": "Santarém",
    "sao pedro do sul": "Viseu",
    "setubal": "Setúbal",
    "silves": "Faro",
    "sintra": "Lisboa",
    "tires": "Lisboa",
    "torres novas": "Santarém",
    "vale de judeus": "Lisboa",
    "viana do castelo": "Viana do Castelo",
    "vila do conde": "Porto",
    "vila real": "Vila Real",
    "viseu": "Viseu",
    # extra safety for common EP towns
    "monsanto - lisboa": "Lisboa",
    "santa cruz do bispo": "Porto",
    "sao joao de deus": "Lisboa",
    "seroa-pacos de ferreira": "Porto",
    "vale do sousa": "Porto",
    "leiria - jovens": "Leiria",
}


def fetch(url, tries=3, delay=0.4):
    """Fetch a URL politely, retrying transient failures."""
    ctx = None
    cert = os.environ.get("SSL_CERT_FILE")
    if cert and os.path.exists(cert):
        ctx = ssl.create_default_context(cafile=cert)
    last_err = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40, context=ctx) as resp:
                data = resp.read()
            time.sleep(delay)
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last_err))


def strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</(p|div|li|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def norm_key(value):
    """Lowercase + strip accents (stdlib-only, small manual table)."""
    table = str.maketrans(
        "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
        "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC",
    )
    return value.translate(table).lower().strip()


def extract_links(listing_html, path_marker, min_depth=1):
    """Return ordered unique (url, name) for detail links under the marker.

    min_depth = number of path segments a detail page must have below the
    marker (EP pages sit two levels down, under a TEP-area segment; region
    index pages sit only one level down and are skipped).
    """
    seen = {}
    pattern = re.compile(
        r'href="([^"]*' + re.escape(path_marker) + r'/[^"]+)"[^>]*>([^<]+)',
        re.I,
    )
    for href, label in pattern.findall(listing_html):
        raw_path = urllib.parse.urlsplit(html.unescape(href)).path
        decoded = urllib.parse.unquote(raw_path)
        tail = decoded.split(path_marker + "/", 1)[1]
        if len([seg for seg in tail.split("/") if seg]) < min_depth:
            continue
        label = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not label or label.lower().startswith("área territorial"):
            continue
        url = BASE + urllib.parse.quote(decoded, safe="/")
        key = decoded.lower()
        if key not in seen:
            seen[key] = (url, label)
    return list(seen.values())


def block_after(page, label):
    """Return the <ul> list items that follow the <h4> whose text is label.

    Matched on the visible heading text (not the id attribute) because the
    site's anchors are inconsistent (e.g. id="porfax" on a telephone h4).
    """
    match = re.search(
        r"<h4[^>]*>\s*" + re.escape(label) + r"\s*</h4>.*?<ul>(.*?)</ul>",
        page,
        re.S | re.I,
    )
    if not match:
        return []
    items = re.findall(r"<li[^>]*>(.*?)</li>", match.group(1), re.S | re.I)
    return [" ".join(strip_tags(item)) for item in items]


def parse_phone_list(items):
    numbers = []
    for item in items:
        for num in re.findall(r"\(?\+?351\)?[\s\d]{9,}|\b\d{3}\s?\d{3}\s?\d{3}\b", item):
            cleaned = re.sub(r"[^\d+]", "", num)
            cleaned = cleaned.lstrip("+")
            if cleaned.startswith("351"):
                cleaned = cleaned[3:]
            if len(cleaned) == 9 and cleaned not in numbers:
                numbers.append(cleaned)
    return "; ".join(numbers)


def parse_email(page):
    match = re.search(r'id="poremail".*?<ul>(.*?)</ul>', page, re.S | re.I)
    scope = match.group(1) if match else page
    found = re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", scope)
    return found[0].lower() if found else ""


def parse_address(page):
    match = re.search(
        r'id="Morada".*?<ul>(.*?)</ul>', page, re.S | re.I
    )
    if not match:
        return "", "", ""
    lines = strip_tags(match.group(1))
    postal, locality = "", ""
    addr_lines = []
    for line in lines:
        pc = re.match(r"^(\d{4}\s*[-–]\s*\d{3})\s*(.*)$", line)
        if pc:
            postal = re.sub(r"\s*[-–]\s*", "-", pc.group(1))
            locality = pc.group(2).strip(" .,;-")
        else:
            addr_lines.append(line)
    address = ", ".join(addr_lines)
    return address, postal, locality


def parse_coords(page):
    match = re.search(r"maps/embed\?pb=([^\"']+)", page)
    if not match:
        return "", ""
    pb = html.unescape(match.group(1))
    lng = re.search(r"!2d(-?\d+\.\d+)", pb)
    lat = re.search(r"!3d(-?\d+\.\d+)", pb)
    if lat and lng:
        return lat.group(1), lng.group(1)
    return "", ""


def district_for(locality, name):
    clean_name = re.sub(r"\s*\([^)]*\)", "", name or "").strip()
    for probe in (locality, name, clean_name):
        key = norm_key(probe or "")
        if key in DISTRICT_BY_LOCALITY:
            return DISTRICT_BY_LOCALITY[key]
    # try trailing tokens of a name such as "Estabelecimento Prisional de X"
    for source in (norm_key(clean_name), norm_key(locality or "")):
        words = source.split()
        for start in range(len(words)):
            tail = " ".join(words[start:])
            if tail in DISTRICT_BY_LOCALITY:
                return DISTRICT_BY_LOCALITY[tail]
    return ""


def scrape_detail(url, name, category):
    page = fetch(url)
    address, postal, locality = parse_address(page)
    phone = parse_phone_list(block_after(page, "por telefone"))
    fax = parse_phone_list(block_after(page, "por fax"))
    email = parse_email(page)
    lat, lng = parse_coords(page)
    return {
        "name": name,
        "category": category,
        "address": address,
        "postal_code": postal,
        "locality": locality,
        "district_or_region": district_for(locality, name),
        "phone": phone,
        "fax": fax,
        "email": email,
        "website": "",
        "latitude": lat,
        "longitude": lng,
        "source_url": url,
    }


def main():
    rows = []
    for list_url, marker, min_depth, category in (
        (EP_LIST_URL, "Estabelecimentos-prisionais", 2, "estabelecimento_prisional"),
        (CE_LIST_URL, "Centros-educativos", 1, "centro_educativo"),
    ):
        listing = fetch(list_url)
        links = extract_links(listing, marker, min_depth)
        print("%s -> %d entries" % (category, len(links)), file=sys.stderr)
        for url, name in links:
            row = scrape_detail(url, name, category)
            rows.append(row)
            print("  scraped: %s" % name, file=sys.stderr)

    rows.sort(key=lambda r: (r["category"], norm_key(r["name"])))

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "prisoes.csv")
    json_path = os.path.join(OUT_DIR, "prisoes.json")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)

    print("wrote %d rows -> %s / %s" % (len(rows), csv_path, json_path))
    missing_district = [r["name"] for r in rows if not r["district_or_region"]]
    missing_addr = [r["name"] for r in rows if not r["address"] and not r["postal_code"]]
    if missing_district:
        print("no district for: %s" % "; ".join(missing_district))
    if missing_addr:
        print("no address for: %s" % "; ".join(missing_addr))


if __name__ == "__main__":
    main()
