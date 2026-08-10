"""Scrape the Citius justice-directory (ContactosTribunais.aspx).

One ASP.NET WebForms postback per localidade returns every justice-system
entity there (courts, public prosecution, julgados de paz, registries,
prisons, ...). A JSON page method (GetMapInfo) yields GPS coordinates per
entity where the portal has them.

Python stdlib only. Outputs data/directory.csv and data/directory.json.
"""

import csv
import html as htmllib
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request

URL = "https://www.citius.mj.pt/portal/ContactosTribunais.aspx"
MAP_URL = URL + "/GetMapInfo"
REQUEST_DELAY = 0.3

COLUMNS = [
    "localidade", "entity_id", "name", "category", "address", "postal_code",
    "phone", "fax", "email", "latitude", "longitude",
]

# Category from name keywords — first match wins.
CATEGORIES = [
    ("julgado de paz", "julgado_de_paz"),
    ("ministério público", "ministerio_publico"),
    ("procuradoria", "ministerio_publico"),
    ("dciap", "ministerio_publico"),
    ("diap", "ministerio_publico"),
    ("contencioso do estado", "ministerio_publico"),
    ("tribunal", "tribunal"),
    ("juízo", "tribunal"),
    ("conservatória", "registos_notariado"),
    ("registo", "registos_notariado"),
    ("notariado", "registos_notariado"),
    ("cartório", "registos_notariado"),
    ("prisional", "estabelecimento_prisional"),
    ("reinserção", "reinsercao_social"),
    ("medicina legal", "medicina_legal"),
]


def build_opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")]
    return op


def fetch_page(op):
    body = op.open(URL, timeout=60).read().decode("utf-8", "replace")
    vs = re.search(r'id="__VIEWSTATE" value="([^"]*)"', body).group(1)
    vsg = re.search(r'id="__VIEWSTATEGENERATOR" value="([^"]*)"', body).group(1)
    sel = re.search(r'ddlLocalidade.*?</select>', body, re.S).group(0)
    # Option values HTML-encode spaces (&#32;) — decode to post the exact
    # underlying value, trailing spaces and all.
    locs = [htmllib.unescape(v)
            for v in re.findall(r'<option[^>]* value="([^"]+)"', sel) if v != "0"]
    return vs, vsg, locs


def postback(op, vs, vsg, localidade):
    data = urllib.parse.urlencode({
        "ctl00$ContentPlaceHolder1$ScriptManager1":
            "ctl00$ContentPlaceHolder1$ScriptManager1|ctl00$ContentPlaceHolder1$ddlLocalidade",
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlLocalidade",
        "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg, "__VIEWSTATEENCRYPTED": "",
        "ctl00$ContentPlaceHolder1$ddlLocalidade": localidade,
        "__ASYNCPOST": "true",
    }).encode()
    req = urllib.request.Request(URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-MicrosoftAjax": "Delta=true",
        "X-Requested-With": "XMLHttpRequest",
    })
    return op.open(req, timeout=60).read().decode("utf-8", "replace")


def clean(text):
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def categorize(name):
    lower = name.lower()
    for needle, cat in CATEGORIES:
        if needle in lower:
            return cat
    return "outro"


def parse_entities(delta, localidade):
    entities = []
    for block in re.split(r'<div class="pesquisaresultado">', delta)[1:]:
        m = re.search(r"<h1>\s*(.*?)\s*</h1>", block, re.S)
        if not m:
            continue
        name = clean(re.sub(r"<br\s*/?>", " — ", m.group(1)))

        m = re.search(r"<strong>Morada:\s*</strong>\s*(.*?)<br", block, re.S)
        address = clean(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""
        pm = re.search(r"(\d{4}-\d{3})\s*([^,]*)$", address)
        postal = pm.group(1) if pm else ""

        m = re.search(r"<strong>Telefone:\s*</strong>\s*([\d /;.-]+)", block)
        phone = clean(m.group(1)) if m else ""
        m = re.search(r"<strong>Fax:\s*</strong>\s*([\d /;.-]+)", block)
        fax = clean(m.group(1)) if m else ""
        m = re.search(r'mailto:([^"]+)"', block)
        email = clean(m.group(1)) if m else ""
        m = re.search(r"map_gps_(\d+)", block)
        entity_id = m.group(1) if m else ""

        entities.append({
            "localidade": localidade.strip(),
            "entity_id": entity_id,
            "name": name,
            "category": categorize(name),
            "address": address,
            "postal_code": postal,
            "phone": phone,
            "fax": fax,
            "email": email,
            "latitude": "",
            "longitude": "",
        })
    return entities


def fetch_coords(entity_id):
    req = urllib.request.Request(
        MAP_URL, data=json.dumps({"id": int(entity_id)}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "Mozilla/5.0"})
    try:
        outer = json.loads(urllib.request.urlopen(req, timeout=60).read())
        info = json.loads(outer["d"])
    except Exception as e:
        print(f"    GetMapInfo({entity_id}) failed: {e}")
        return "", ""
    if not info.get("HasCoordinates"):
        return "", ""
    return str(info.get("Latitude", "")), str(info.get("Longitude", ""))


def main():
    op = build_opener()
    vs, vsg, locs = fetch_page(op)
    print(f"{len(locs)} localidades")

    rows = []
    for i, loc in enumerate(locs, 1):
        time.sleep(REQUEST_DELAY)
        for attempt in range(3):
            try:
                delta = postback(op, vs, vsg, loc)
                break
            except Exception as e:
                print(f"  {loc}: attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (attempt + 1))
                if attempt == 2:
                    delta = ""
        entities = parse_entities(delta, loc)
        rows.extend(entities)
        print(f"  [{i}/{len(locs)}] {loc.strip()}: {len(entities)} entities")

    seen = set()
    unique = []
    for r in rows:
        key = (r["entity_id"], r["name"], r["localidade"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    print(f"{len(unique)} unique entities — fetching coordinates...")
    for i, r in enumerate(unique, 1):
        if not r["entity_id"]:
            continue
        time.sleep(0.15)
        r["latitude"], r["longitude"] = fetch_coords(r["entity_id"])
        if i % 100 == 0:
            print(f"  coords {i}/{len(unique)}")

    unique.sort(key=lambda r: (r["localidade"], r["category"], r["name"]))
    with open("data/directory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(unique)
    with open("data/directory.json", "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=1)

    by_cat = {}
    for r in unique:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"Wrote {len(unique)} entities: " +
          ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))


if __name__ == "__main__":
    sys.exit(main())
