"""Scrape the directory of Julgados de Paz from the Conselho dos Julgados
de Paz (conselhodosjulgadosdepaz.com.pt).

The Conselho is the official oversight body for the julgados de paz and its
site has one contact page per julgado / attendance point
(contactos_XXX.asp).  Each page embeds its data as JS variables in <head>:

    endereco = "correio.tarouca";        # e-mail local part
    dominio  = "julgadosdepaz.mj.pt";    # e-mail domain
    nome     = "Tarouca";                # seat concelho
    titulo   = "<h3>Julgado de Paz de Tarouca</h3><h4>Agrupamento de ...</h4>";
    m        = "<p>Avenida ...<br />3610-130 Tarouca</p>";   # address
    t/tt     = phone (raw / formatted); f = fax
    lat/lng  = coordinates

Python stdlib only.  Outputs data/julgados_paz.csv and data/julgados_paz.json.
"""

import csv
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://www.conselhodosjulgadosdepaz.com.pt"
INDEX_URL = BASE + "/contactos.asp"
REQUEST_DELAY = 0.35
TIMEOUT = 30
RETRIES = 3

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

COLUMNS = [
    "name", "category", "address", "postal_code", "locality",
    "district_or_region", "phone", "fax", "email", "website",
    "latitude", "longitude", "source_url",
]

CATEGORY = "julgado_de_paz"

# Seat concelho -> district (or autonomous region).  Static administrative
# mapping, not geocoding.
DISTRICTS = {
    "Aguiar da Beira": "Guarda",
    "Alcobaça": "Leiria",
    "Alenquer": "Lisboa",
    "Aljustrel": "Beja",
    "Arruda dos Vinhos": "Lisboa",
    "Belmonte": "Castelo Branco",
    "Bombarral": "Leiria",
    "Cadaval": "Lisboa",
    "Caldas da Rainha": "Leiria",
    "Cantanhede": "Coimbra",
    "Carregal do Sal": "Viseu",
    "Cascais": "Lisboa",
    "Castro Verde": "Beja",
    "Coimbra": "Coimbra",
    "Figueiró dos Vinhos": "Leiria",
    "Funchal": "Madeira",
    "Lisboa": "Lisboa",
    "Lourinhã": "Lisboa",
    "Mira": "Coimbra",
    "Miranda do Corvo": "Coimbra",
    "Moimenta da Beira": "Viseu",
    "Montemor-o-Velho": "Coimbra",
    "Nazaré": "Leiria",
    "Óbidos": "Leiria",
    "Odivelas": "Lisboa",
    "Oliveira do Bairro": "Aveiro",
    "Palmela": "Setúbal",
    "Peniche": "Leiria",
    "Porto": "Porto",
    "Proença-a-Nova": "Castelo Branco",
    "Santa Maria da Feira": "Aveiro",
    "Santa Marta de Penaguião": "Vila Real",
    "Santo Tirso": "Porto",
    "Seixal": "Setúbal",
    "Sertã": "Castelo Branco",
    "Setúbal": "Setúbal",
    "Sintra": "Lisboa",
    "Sobral de Monte Agraço": "Lisboa",
    "Tarouca": "Viseu",
    "Terras de Bouro": "Braga",
    "Torres Vedras": "Lisboa",
    "Trancoso": "Guarda",
    "Trofa": "Porto",
    "Vila de Rei": "Castelo Branco",
    "Vila Nova de Gaia": "Porto",
    "Vila Nova de Paiva": "Viseu",
    "Vila Nova de Poiares": "Coimbra",
    "Vila Real": "Vila Real",
}


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; data-pt-justice-directory)",
    })
    last_err = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as err:
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last_err))


def js_var(page, name):
    """Value of a JS string variable assignment: name = "...";"""
    m = re.search(r'\b%s\s*=\s*"((?:[^"\\]|\\.)*)"\s*;' % re.escape(name), page)
    if not m:
        return ""
    return m.group(1).replace('\\"', '"').replace("\\/", "/")


def js_num(page, name):
    m = re.search(r'\b%s\s*=\s*(-?\d+(?:\.\d+)?)\s*;' % re.escape(name), page)
    return m.group(1) if m else ""


def strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</p>\s*<p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return htmllib.unescape(text)


def parse_body(page):
    """Fallback for the newer page template: data lives in the HTML body,
    not in JS variables.  Returns (name, address_html, phone, fax, email)."""
    m = re.search(
        r"<h3>\s*(Julgado de Paz[^<]*)</h3>(?:\s*<h4>(.*?)</h4>)?", page)
    if not m:
        return None
    name = htmllib.unescape(m.group(1)).strip()
    subtitle = strip_tags(m.group(2)).strip() if m.group(2) else ""
    if subtitle:
        name = "%s (%s)" % (name, subtitle)
    addr = re.search(r"Endereço</h4>\s*<p>(.*?)</p>", page, re.S)
    address_html = addr.group(1) if addr else ""
    # Drop trailing "Mapa" link inside the address paragraph.
    address_html = re.sub(r"<a\b.*?</a>", "", address_html, flags=re.S)
    tel = re.search(r'href="tel:([^"]+)"', page)
    mail = re.search(r'href="mailto:([^"]+)"', page)
    fax = re.search(r"Fax[:\s]*</?[^>]*>?\s*([\d ]{9,})", page)
    return (name, address_html,
            htmllib.unescape(tel.group(1)).strip() if tel else "",
            fax.group(1).strip() if fax else "",
            htmllib.unescape(mail.group(1)).strip() if mail else "")


def parse_page(page, url):
    titulo = js_var(page, "titulo")
    if titulo:
        h3 = re.search(r"<h3>(.*?)</h3>", titulo)
        h4 = re.search(r"<h4>(.*?)</h4>", titulo)
        name = (strip_tags(h3.group(1)).strip() if h3
                else strip_tags(titulo).strip())
        if h4:
            extra = strip_tags(h4.group(1)).strip()
            if extra:
                name = "%s (%s)" % (name, extra)
        address_html = js_var(page, "m")
        phone = js_var(page, "tt").strip() or js_var(page, "t").strip()
        fax = js_var(page, "f").strip()
        local_part = js_var(page, "endereco")
        domain = js_var(page, "dominio")
        email = ("%s@%s" % (local_part, domain)
                 if local_part and domain else "")
    else:
        body = parse_body(page)
        if body is None:
            return None
        name, address_html, phone, fax, email = body
    # Normalise "Julgado de Paz: X" -> "Julgado de Paz de X".
    name = re.sub(r"^Julgado de Paz:\s*", "Julgado de Paz de ", name)

    addr_lines = [l.strip() for l in strip_tags(address_html).splitlines()
                  if l.strip()]
    postal_code, locality = "", ""
    if addr_lines:
        pm = re.match(r"(\d{4})\s*[-–—]\s*(\d{3})\s+(.+)$",
                      addr_lines[-1])
        if pm:
            postal_code = "%s-%s" % (pm.group(1), pm.group(2))
            locality = pm.group(3).strip()
            addr_lines = addr_lines[:-1]
    address = ", ".join(addr_lines)

    nome = strip_tags(js_var(page, "nome")).strip()
    seat = ""
    sm = re.match(r"Julgado de Paz (?:de |da |do |das |dos )?([^(]+)", name)
    if sm:
        seat = sm.group(1).strip()
    district = (DISTRICTS.get(nome) or DISTRICTS.get(seat)
                or DISTRICTS.get(locality, ""))
    if not district:
        print("WARNING: no district for seat %r (%s)" % (nome or locality, url),
              file=sys.stderr)

    return {
        "name": name,
        "category": CATEGORY,
        "address": address,
        "postal_code": postal_code,
        "locality": locality or nome,
        "district_or_region": district,
        "phone": phone,
        "fax": fax,
        "email": email,
        "website": "",
        "latitude": js_num(page, "lat"),
        "longitude": js_num(page, "lng"),
        "source_url": url,
    }


def main():
    index = fetch(INDEX_URL)
    slugs = sorted(set(re.findall(r"contactos_[A-Z]+\.asp", index)))
    print("contact pages found: %d" % len(slugs))
    if len(slugs) < 40:
        raise RuntimeError("suspiciously few contact pages: %d" % len(slugs))

    rows = []
    for slug in slugs:
        time.sleep(REQUEST_DELAY)
        url = "%s/%s" % (BASE, slug)
        row = parse_page(fetch(url), url)
        if row is None:
            print("WARNING: no data block on %s" % url, file=sys.stderr)
            continue
        if not row["name"]:
            print("WARNING: empty name on %s" % url, file=sys.stderr)
            continue
        rows.append(row)

    rows.sort(key=lambda r: r["name"])

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "julgados_paz.csv")
    json_path = os.path.join(OUT_DIR, "julgados_paz.json")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("rows written: %d" % len(rows))
    print("districts/regions: %s" %
          sorted({r["district_or_region"] for r in rows}))
    print("with coordinates: %d" %
          sum(1 for r in rows if r["latitude"] and r["longitude"]))
    print("with email: %d" % sum(1 for r in rows if r["email"]))


if __name__ == "__main__":
    main()
