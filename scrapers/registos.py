"""Scrape Portugal's registries & notaries directory (family: REGISTOS E NOTARIADO).

Sources
-------
1. IRN — Instituto dos Registos e do Notariado (irn.justica.gov.pt/Contactos/Lista).
   The page is a DotNetNuke FAQs module: each "question" is a locality and its
   "answer" (fetched through the classic DNN client-callback endpoint
   __DNNCAPISCI/__DNNCAPISCP) links to one page per registry office
   (conservatorias do registo civil / predial / comercial / automoveis,
   espacos registos, lojas de cidadao).  A WebForms postback per district on
   the category dropdown gives the authoritative locality->district mapping.
   Each office page carries address, postal code, phone, fax, email and a
   Google-Maps link whose URL embeds the coordinates published by IRN.
   Category slug: 'conservatoria'.

2. DRAJ Madeira (madeira.gov.pt/draj) — registries in the Autonomous Region of
   Madeira are run by the regional government; IRN's own Madeira entry points
   to this page.  One structured page lists all RAM conservatorias.
   Category slug: 'conservatoria'.

3. Ordem dos Notarios (ordem.notarios.pt/notarios) — official roster of public
   notaries; rows with an active cartorio link to a notarios.pt profile page
   with morada, phone, fax, email, distrito, concelho and coordinates
   (static-map center).  Category slug: 'cartorio_notarial'.

Python stdlib only.  Outputs data/registos.csv and data/registos.json.

Optional env var REGISTOS_CACHE=<dir> caches raw HTTP responses on disk so an
interrupted run can resume without re-hitting the sites.
"""

import csv
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")

IRN_LIST_URL = "https://irn.justica.gov.pt/Contactos/Lista"
DRAJ_URL = ("https://www.madeira.gov.pt/draj/Estrutura/DRAJ/Contactos/ctl/Read/"
            "mid/13251/InformacaoId/125310/UnidadeOrganicaId/51/CatalogoId/0")
ON_LIST_URL = "https://ordem.notarios.pt/notarios"

USER_AGENT = "Mozilla/5.0 (compatible; registos-directory-scraper)"
REQUEST_DELAY = 0.3
TIMEOUT = 40
RETRIES = 3

COLUMNS = [
    "name", "category", "address", "postal_code", "locality",
    "district_or_region", "phone", "fax", "email", "website",
    "latitude", "longitude", "source_url",
]

CACHE_DIR = os.environ.get("REGISTOS_CACHE") or ""
if CACHE_DIR:
    os.makedirs(CACHE_DIR, exist_ok=True)

_last_request = [0.0]


def fetch(url, data=None, retries=RETRIES):
    """Polite GET/POST with retries and optional on-disk cache."""
    key = None
    if CACHE_DIR:
        key = os.path.join(
            CACHE_DIR,
            hashlib.sha1((url + "|" + (data or "")).encode()).hexdigest() + ".bin")
        if os.path.exists(key):
            with open(key, "rb") as fh:
                return fh.read().decode("utf-8", "replace")
    last_err = None
    for attempt in range(retries):
        wait = REQUEST_DELAY - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(
                url,
                data=data.encode() if data else None,
                headers={"User-Agent": USER_AGENT})
            body = urllib.request.urlopen(req, timeout=TIMEOUT).read()
            _last_request[0] = time.time()
            if key:
                with open(key, "wb") as fh:
                    fh.write(body)
            return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            _last_request[0] = time.time()
            if err.code in (404, 410):
                raise
            last_err = err
        except Exception as err:  # URLError, timeout, ConnectionRefused...
            _last_request[0] = time.time()
            last_err = err
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %r" % (url, last_err))


def strip_tags(fragment):
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(p|div|li|h\d|tr)>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = htmllib.unescape(fragment)
    fragment = fragment.replace("\xa0", " ").replace("\u200b", "")
    return fragment


def clean_line(text):
    return re.sub(r"\s+", " ", text).strip(" \t-\u2013,;")


POSTAL_RE = re.compile(r"(\d{4})\s*[-\u2013]\s*(\d{3})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
COORD_RES = [
    re.compile(r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&](?:q|center|ll)=(-?\d{1,2}\.\d+)\s*(?:,|%2C)\s*(-?\d{1,3}\.\d+)"),
]


def find_coords(fragment):
    for pat in COORD_RES:
        m = pat.search(fragment)
        if m:
            return m.group(1), m.group(2)
    return "", ""


def hidden_field(page, name):
    m = re.search(r'id="%s" value="([^"]*)"' % name, page)
    return m.group(1) if m else ""


FAQ_ITEM_RE = re.compile(
    r"SetAnswerLabel\(&#39;DNN_FAQ_19451_(\d+)&#39;\).*?"
    r"padding-right: 24px;\">\s*(.*?)\s*</div>", re.S)


# --------------------------------------------------------------------------- IRN
def scrape_irn():
    page = fetch(IRN_LIST_URL)

    items = [(fid, clean_line(htmllib.unescape(title)))
             for fid, title in FAQ_ITEM_RE.findall(page)]
    print("IRN: %d localities on the FAQ list" % len(items), flush=True)

    # district dropdown (module 19451)
    sel = re.search(
        r'<select name="dnn\$ctr19451\$FAQs\$drpCategories".*?</select>', page, re.S)
    options = re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>', sel.group(0))
    options = [(v, clean_line(htmllib.unescape(t))) for v, t in options]
    print("IRN: %d district categories" % len(options), flush=True)

    hidden = {
        "__VIEWSTATE": hidden_field(page, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": hidden_field(page, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": hidden_field(page, "__EVENTVALIDATION"),
    }

    faq_district = {}
    for value, district in options:
        form = dict(hidden)
        form.update({
            "__EVENTTARGET": "dnn$ctr19451$FAQs$drpCategories",
            "__EVENTARGUMENT": "",
            "dnn$ctr19451$FAQs$drpCategories": value,
        })
        body = fetch(IRN_LIST_URL, data=urllib.parse.urlencode(form))
        got = FAQ_ITEM_RE.findall(body)
        for fid, _ in got:
            faq_district[fid] = district
        print("IRN district %-16s -> %3d localities" % (district, len(got)), flush=True)

    # answer callbacks -> office links
    offices = {}  # url -> dict(name, district, locality)
    plain_answers = []
    for fid, locality in items:
        payload = urllib.parse.urlencode({
            "__DNNCAPISCI": "FAQs dnn_ctr19451_FAQs",
            "__DNNCAPISCP": fid,
        })
        raw = fetch(IRN_LIST_URL, data=payload)
        try:
            answer = json.loads(raw)["d"]
        except (ValueError, KeyError):
            answer = raw
        answer = answer.split("Visualizado:")[0]
        links = re.findall(r'<a href="(https?://[^"]+)"[^>]*>(.*?)</a>', answer, re.S)
        n = 0
        for href, text in links:
            href = htmllib.unescape(href).strip()
            if "irn.justica.gov.pt" not in href:
                continue  # e.g. Madeira -> madeira.gov.pt, handled separately
            name = clean_line(strip_tags(text))
            if not name:
                continue
            offices.setdefault(href, {
                "name": name,
                "district": faq_district.get(fid, ""),
                "locality": locality,
            })
            n += 1
        if n == 0:
            plain_answers.append((locality, clean_line(strip_tags(answer))[:120]))
    print("IRN: %d unique office pages; %d localities with no office link"
          % (len(offices), len(plain_answers)), flush=True)
    for loc, preview in plain_answers:
        print("  no-link locality: %s | %s" % (loc, preview), flush=True)

    rows = []
    done = 0
    for url, meta in sorted(offices.items()):
        row = {c: "" for c in COLUMNS}
        row.update({
            "name": meta["name"],
            "category": "conservatoria",
            "district_or_region": meta["district"],
            "locality": meta["locality"],
            "source_url": url,
        })
        try:
            page = fetch(url)
        except urllib.error.HTTPError as err:
            print("  office page %s -> HTTP %s (kept name only)" % (url, err.code),
                  flush=True)
            rows.append(row)
            continue
        parse_irn_office(page, row)
        rows.append(row)
        done += 1
        if done % 50 == 0:
            print("IRN: %d/%d office pages parsed" % (done, len(offices)), flush=True)
    return rows


def parse_irn_office(page, row):
    # contact block: from the "Contactos" heading up to Horario / services
    m = re.search(r">\s*Contactos\s*<", page)
    seg = page[m.start():m.start() + 6000] if m else page
    for stop in (r">\s*Hor&#225;rio", r">\s*Hor\u00e1rio", r">\s*Horário",
                 r"O que posso fazer"):
        s = re.search(stop, seg)
        if s:
            seg = seg[:s.start()]
            break

    lat, lng = find_coords(seg)
    if not lat:
        lat, lng = find_coords(page)
    row["latitude"], row["longitude"] = lat, lng

    text = strip_tags(seg)
    lines = [clean_line(l) for l in text.split("\n")]
    lines = [l for l in lines if l and l.lower() not in ("contactos", "mapa", "( mapa )")]

    emails, phones, faxes, addr_lines = [], [], [], []
    for line in lines:
        low = line.lower()
        found_emails = EMAIL_RE.findall(line)
        if found_emails:
            emails.extend(found_emails)
            continue
        if low.startswith("telefone") or low.startswith("tel."):
            phones.append(clean_line(re.sub(r"(?i)^tel(efone)?\.?:?", "", line)))
            continue
        if low.startswith("fax"):
            faxes.append(clean_line(re.sub(r"(?i)^fax:?", "", line)))
            continue
        if low.startswith(("email", "e-mail")):
            continue
        addr_lines.append(line)

    address = clean_line(" ".join(addr_lines))
    address = re.sub(r"\(\s*mapa\s*\)", "", address, flags=re.I)
    address = clean_line(address)
    pm = POSTAL_RE.search(address)
    if pm:
        row["postal_code"] = "%s-%s" % (pm.group(1), pm.group(2))
        tail = clean_line(address[pm.end():])
        tail = re.sub(r"[(].*$", "", tail).strip()
        if tail:
            row["locality"] = clean_line(tail)
        address = clean_line(address[:pm.start()])
    row["address"] = address
    row["phone"] = "; ".join(dict.fromkeys(p for p in phones if p))
    row["fax"] = "; ".join(dict.fromkeys(f for f in faxes if f))
    row["email"] = "; ".join(dict.fromkeys(emails))


# ----------------------------------------------------------------- DRAJ Madeira
def scrape_madeira():
    page = fetch(DRAJ_URL)
    body = strip_tags(page)
    lines = [clean_line(l) for l in body.split("\n")]
    lines = [l for l in lines if l]

    rows = []
    current = None
    for line in lines:
        if re.match(r"(?i)^conservat[oó]ria", line) and len(line) > 20:
            if current:
                rows.append(current)
            current = {c: "" for c in COLUMNS}
            current.update({
                "name": line,
                "category": "conservatoria",
                "district_or_region": "Madeira",
                "source_url": DRAJ_URL,
            })
            current["_lines"] = []
        elif current is not None:
            if line.lower().startswith("autor:"):
                rows.append(current)
                current = None
            else:
                current["_lines"].append(line)
    if current:
        rows.append(current)

    for row in rows:
        lines = row.pop("_lines")
        addr, phones, emails = [], [], []
        in_addr = False
        for line in lines:
            low = line.lower()
            if low.startswith("morada"):
                in_addr = True
                continue
            if low.startswith(("mapa", "contatos", "contactos")):
                in_addr = False
                continue
            if low.startswith(("horário", "horario", "dias úteis", "dias uteis")):
                in_addr = False
                continue
            found = EMAIL_RE.findall(line)
            if found:
                emails.extend(found)
                continue
            if re.match(r"(?i)^t\.?\s*\+?\d?", line) and "291" in line:
                phones.append(clean_line(re.sub(r"(?i)^t\.?", "", line)))
                continue
            if in_addr:
                addr.append(line)
        address = clean_line(" ".join(addr))
        pm = POSTAL_RE.search(address)
        if pm:
            row["postal_code"] = "%s-%s" % (pm.group(1), pm.group(2))
            tail = clean_line(address[pm.end():])
            if tail:
                row["locality"] = tail
            address = clean_line(address[:pm.start()])
        row["address"] = address
        row["phone"] = "; ".join(dict.fromkeys(phones))
        row["email"] = "; ".join(dict.fromkeys(emails))
    print("Madeira DRAJ: %d conservatorias" % len(rows), flush=True)
    return rows


# --------------------------------------------------------------- Ordem Notarios
AZORES_ISLANDS = {
    "sao miguel", "são miguel", "terceira", "faial", "pico", "sao jorge",
    "são jorge", "graciosa", "flores", "corvo", "santa maria", "acores", "açores",
}


def norm_notary_district(value):
    v = clean_line(value)
    low = v.lower().replace("ilha de ", "").replace("ilha da ", "").replace("ilha do ", "")
    if low in AZORES_ISLANDS:
        return "Açores"
    if "madeira" in low or low in ("porto santo",):
        return "Madeira"
    return v


def scrape_notaries():
    page = fetch(ON_LIST_URL)
    entries = re.findall(
        r'<td class="font-normal">([^<]+)</td>\s*<td class="font-normal">\s*<p>\s*'
        r'<a href="(https://notarios\.pt/notarios/[^"]+)"[^>]*>.*?</i>\s*([^<]+)</a>',
        page, re.S)
    seen = {}
    for name, href, town in entries:
        seen.setdefault(href, (clean_line(htmllib.unescape(name)),
                               clean_line(htmllib.unescape(town))))
    print("Notaries: %d cartorios listed" % len(seen), flush=True)

    rows = []
    done = 0
    for href, (name, town) in sorted(seen.items()):
        row = {c: "" for c in COLUMNS}
        row.update({
            "name": name,
            "category": "cartorio_notarial",
            "locality": town,
            "source_url": href,
        })
        try:
            page = fetch(href)
        except urllib.error.HTTPError as err:
            print("  notary page %s -> HTTP %s (kept name only)" % (href, err.code),
                  flush=True)
            rows.append(row)
            continue
        parse_notary(page, row)
        rows.append(row)
        done += 1
        if done % 50 == 0:
            print("Notaries: %d/%d profile pages parsed" % (done, len(seen)),
                  flush=True)
    return rows


def field_block(page, label):
    m = re.search(
        r">\s*%s:\s*</strong>\s*(?:<span[^>]*>|<div[^>]*>)?(.*?)</(?:span|div)>" % label,
        page, re.S)
    return clean_line(strip_tags(m.group(1))) if m else ""


def parse_notary(page, row):
    m = re.search(r">\s*Morada:\s*</strong>\s*<div[^>]*>(.*?)</div>", page, re.S)
    if m:
        paras = [clean_line(strip_tags(p))
                 for p in re.findall(r"<p>(.*?)</p>", m.group(1), re.S)]
        paras = [p for p in paras if p and p.lower() != "portugal"]
        postal_line = ""
        addr = []
        for p in paras:
            if not postal_line and POSTAL_RE.search(p):
                postal_line = p
            else:
                addr.append(p)
        if postal_line:
            pm = POSTAL_RE.search(postal_line)
            row["postal_code"] = "%s-%s" % (pm.group(1), pm.group(2))
            tail = clean_line(postal_line[pm.end():])
            if tail:
                row["locality"] = tail
        row["address"] = clean_line(", ".join(addr))

    for label, col in (("Telefone", "phone"), ("Fax", "fax"), ("Email", "email"),
                       ("Concelho", "_concelho"), ("Distrito", "_distrito")):
        val = field_block(page, label)
        if not val:
            continue
        val = re.sub(r"\(Chamada para a rede .*?\)", "", val)
        if col.startswith("_"):
            row[col] = clean_line(val)
        else:
            row[col] = clean_line(val)

    if row.get("_concelho"):
        row["locality"] = row.pop("_concelho")
    else:
        row.pop("_concelho", None)
    if row.get("_distrito"):
        row["district_or_region"] = norm_notary_district(row.pop("_distrito"))
    else:
        row.pop("_distrito", None)

    m = re.search(r"staticmap\?center=(-?\d{1,2}\.\d+)(?:%2C|,)(-?\d{1,3}\.\d+)", page)
    if m:
        row["latitude"], row["longitude"] = m.group(1), m.group(2)


# ----------------------------------------------------------------------- output
def main():
    rows = []
    rows.extend(scrape_irn())
    rows.extend(scrape_madeira())
    rows.extend(scrape_notaries())

    for row in rows:
        for extra in [k for k in row if k not in COLUMNS]:
            row.pop(extra)

    os.makedirs(DATA_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_DIR, "registos.csv")
    json_path = os.path.join(DATA_DIR, "registos.json")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)

    by_cat = {}
    by_district = {}
    for row in rows:
        by_cat[row["category"]] = by_cat.get(row["category"], 0) + 1
        key = row["district_or_region"] or "(none)"
        by_district[key] = by_district.get(key, 0) + 1
    print("\nTOTAL rows: %d" % len(rows))
    for cat, n in sorted(by_cat.items()):
        print("  %-20s %d" % (cat, n))
    print("districts: %s" % json.dumps(by_district, ensure_ascii=False, sort_keys=True))
    empty_names = sum(1 for r in rows if not r["name"])
    print("empty names: %d" % empty_names)
    return 0 if rows and not empty_names else 1


if __name__ == "__main__":
    sys.exit(main())
