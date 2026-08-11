#!/usr/bin/env python3
"""Scrape the DGPJ list of authorized institutionalized arbitration centres
(Centros de Arbitragem autorizados) into data/arbitragem.{csv,json}.

Source: https://dgpj.justica.gov.pt/Resolucao-de-Litigios/Arbitragem/Centros-de-Arbitragem-autorizados
DGPJ (Direcao-Geral da Politica de Justica) is the authority that authorizes
arbitration centres in Portugal, so its list is the authoritative registry.

The page is a DotNetNuke FAQ module: each centre is a question whose answer
(address/phone/email/site) is loaded on demand via the DNN Client API callback
(POST to the same URL with __DNNCAPISCI / __DNNCAPISCP form fields).

Python stdlib only.
"""

import csv
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_URL = (
    "https://dgpj.justica.gov.pt/Resolucao-de-Litigios/"
    "Arbitragem/Centros-de-Arbitragem-autorizados"
)
CATEGORY = "centro_arbitragem"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
FIELDS = [
    "name", "category", "address", "postal_code", "locality",
    "district_or_region", "phone", "fax", "email", "website",
    "latitude", "longitude", "source_url",
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

# Locality -> district (mainland). Islands are resolved via postal code.
LOCALITY_DISTRICT = {
    "lisboa": "Lisboa",
    "porto": "Porto",
    "braga": "Braga",
    "guimaraes": "Braga",
    "coimbra": "Coimbra",
    "faro": "Faro",
    "loule": "Faro",
    "obidos": "Leiria",
    "leiria": "Leiria",
    "oeiras": "Lisboa",
    "cascais": "Lisboa",
    "sintra": "Lisboa",
    "amadora": "Lisboa",
    "almancil": "Faro",
    "portimao": "Faro",
    "vila nova de gaia": "Porto",
    "matosinhos": "Porto",
    "maia": "Porto",
    "aveiro": "Aveiro",
    "viseu": "Viseu",
    "evora": "Évora",
    "beja": "Beja",
    "setubal": "Setúbal",
    "santarem": "Santarém",
    "portalegre": "Portalegre",
    "castelo branco": "Castelo Branco",
    "guarda": "Guarda",
    "braganca": "Bragança",
    "vila real": "Vila Real",
    "viana do castelo": "Viana do Castelo",
    "funchal": "Madeira",
    "ponta delgada": "Açores",
    "angra do heroismo": "Açores",
    "horta": "Açores",
}


def _strip_accents(s):
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def make_opener():
    ctx = ssl.create_default_context()
    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile and os.path.exists(cafile):
        ctx = ssl.create_default_context(cafile=cafile)
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


OPENER = make_opener()


def fetch(url, data=None, tries=3, timeout=60):
    """GET (or POST when data given) with retries. data: dict -> urlencoded."""
    body = urllib.parse.urlencode(data).encode("utf-8") if data else None
    last_err = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            })
            with OPENER.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last_err))


def parse_entries(page_html):
    """Return (callback_id, [(faq_id, name), ...]) from the FAQ module page."""
    m = re.search(r"doCallBack\('(FAQs [^']+)'", page_html)
    if not m:
        raise RuntimeError("DNN FAQ callback id not found on page")
    callback_id = m.group(1)
    pat = re.compile(
        r"SetAnswerLabel\(&#39;DNN_FAQ_\d+_(\d+)&#39;\)"
        r".*?padding-right: 24px;\">\s*(.*?)\s*</div>",
        re.S,
    )
    entries = []
    for fm in pat.finditer(page_html):
        faq_id = fm.group(1)
        name = html.unescape(re.sub(r"<[^>]+>", "", fm.group(2))).strip()
        name = re.sub(r"\s+", " ", name)
        if name:
            entries.append((faq_id, name))
    return callback_id, entries


def fetch_answer(callback_id, faq_id):
    raw = fetch(SOURCE_URL, data={
        "__DNNCAPISCI": callback_id,
        "__DNNCAPISCP": faq_id,
    })
    try:
        return json.loads(raw)["d"]
    except (ValueError, KeyError):
        raise RuntimeError("unexpected callback response for id %s" % faq_id)


def html_to_lines(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</(p|div|li|h\d|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return [l for l in lines if l]


LABELS = {
    "address": ("morada", "endereco", "endereço"),
    "phone": ("telefone", "tel.", "tel", "telemovel", "telemóvel", "contacto telefonico"),
    "fax": ("fax",),
    "email": ("email", "e-mail", "correio eletronico", "correio eletrónico"),
    "website": ("site", "sitio", "sítio", "website", "pagina", "página"),
}


def parse_answer_fields(answer_html):
    """Extract first occurrence of each labelled field from an answer."""
    out = {k: "" for k in LABELS}
    for line in html_to_lines(answer_html):
        m = re.match(r"([A-Za-zÀ-ÿ .\-]{2,30})\s*:\s*(.+)$", line)
        if not m:
            continue
        label = _strip_accents(m.group(1)).strip().lower()
        value = m.group(2).strip()
        for field, keys in LABELS.items():
            if label in tuple(_strip_accents(k) for k in keys) and not out[field]:
                out[field] = value
                break
    # tidy phone/fax: keep the first number sequence group
    for f in ("phone", "fax"):
        if out[f]:
            m = re.search(r"(\+?[\d][\d .\-/]{6,})", out[f])
            out[f] = re.sub(r"\s+", " ", m.group(1)).strip(" .-/") if m else out[f]
    if out["email"]:
        m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", out["email"])
        out["email"] = m.group(0) if m else out["email"]
    if out["website"]:
        site = out["website"].split()[0].rstrip(";,")
        if site and not site.lower().startswith(("http://", "https://")):
            site = "https://" + site
        out["website"] = site
    return out


def split_address(address):
    """address -> (street_part, postal_code, locality).

    Some entries list several offices separated by " / "; keep the first
    office that carries a postal code (or the first segment otherwise).
    """
    if " / " in address:
        parts = [p.strip() for p in address.split(" / ") if p.strip()]
        with_cp = [p for p in parts if re.search(r"\d{4}-\d{3}", p)]
        address = (with_cp or parts)[0]
    m = re.search(r"(\d{4}-\d{3})\s*[,–\-]?\s*(.*)$", address)
    if not m:
        addr = address.strip(" ,.")
        # no postal code: try to pull a known locality off the tail
        tail = _strip_accents(addr).lower()
        for name in LOCALITY_DISTRICT:
            if re.search(r"(?:^|[, ])(?:no |em |na )?%s$" % re.escape(name), tail):
                loc = addr[len(addr) - len(name):]
                street = addr[: len(addr) - len(name)].rstrip(" ,")
                street = re.sub(r"(?:,\s*)?(?:no|em|na)$", "", street).rstrip(" ,")
                return street, "", loc
        return addr, "", ""
    street = address[: m.start()].strip(" ,–-")
    postal = m.group(1)
    locality = m.group(2).strip(" ,.;")
    locality = re.sub(r"\s+", " ", locality)
    return street, postal, locality


def district_for(postal_code, locality):
    if postal_code:
        cp4 = int(postal_code[:4])
        if 9000 <= cp4 <= 9499:
            return "Madeira"
        if 9500 <= cp4 <= 9999:
            return "Açores"
    key = _strip_accents(locality).lower().strip()
    # try full locality then its first word-group before slash/comma
    if key in LOCALITY_DISTRICT:
        return LOCALITY_DISTRICT[key]
    for name, dist in LOCALITY_DISTRICT.items():
        if key.startswith(name):
            return dist
    return ""


def main():
    print("Fetching %s" % SOURCE_URL, file=sys.stderr)
    page = fetch(SOURCE_URL)
    callback_id, entries = parse_entries(page)
    print("Found %d centres listed" % len(entries), file=sys.stderr)

    rows = []
    for faq_id, name in entries:
        time.sleep(0.35)
        answer = fetch_answer(callback_id, faq_id)
        fields = parse_answer_fields(answer)
        street, postal, locality = split_address(fields["address"])
        row = {
            "name": name,
            "category": CATEGORY,
            "address": street,
            "postal_code": postal,
            "locality": locality,
            "district_or_region": district_for(postal, locality),
            "phone": fields["phone"],
            "fax": fields["fax"],
            "email": fields["email"],
            "website": fields["website"],
            "latitude": "",
            "longitude": "",
            "source_url": SOURCE_URL,
        }
        rows.append(row)
        print("  %-90s cp=%s loc=%s" % (name[:90], postal, locality),
              file=sys.stderr)

    os.makedirs(DATA_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_DIR, "arbitragem.csv")
    json_path = os.path.join(DATA_DIR, "arbitragem.json")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n_addr = sum(1 for r in rows if r["address"] or r["postal_code"])
    n_mail = sum(1 for r in rows if r["email"])
    regions = sorted({r["district_or_region"] for r in rows if r["district_or_region"]})
    print("Wrote %d rows -> %s / %s" % (len(rows), csv_path, json_path))
    print("with address: %d, with email: %d" % (n_addr, n_mail))
    print("regions: %s" % ", ".join(regions))
    assert all(r["name"] for r in rows), "empty name found"


if __name__ == "__main__":
    main()
