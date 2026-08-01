#!/usr/bin/env python3
"""
Build a self-contained set-completion report from a CollectR CSV export.

    python3 build_binder.py collectr-export.csv -o binder.html

Card checklists come from TCGdex (https://tcgdex.dev). Responses are cached
in ./.tcgdex_cache so re-runs are fast and offline.

Add a new set by putting an entry in SET_MAP below: the exact set name as it
appears in the CollectR CSV, mapped to (language, tcgdex_set_id).
"""

import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.tcgdex.net/v2"
CACHE = ".tcgdex_cache"

# Overrides for CollectR set names that don't match a TCGdex name exactly, or
# that match the wrong one. Everything else resolves automatically against the
# cached set index, so a newly released set usually needs nothing added here.
SET_OVERRIDES = {
    # No TCGdex set carries these names, so they can't resolve on their own.
    "WoTC Promo": ("en", "basep"),            # Wizards Black Star Promos
    "Scarlet & Violet Promo": ("en", "svp"),  # SVP Black Star Promos
    "Mega Evolution Promos": ("en", "mep"),   # MEP Black Star Promos
    # Japanese printings: TCGdex lists these under Japanese names, so the
    # English-looking CollectR label never matches.
    "Pokemon 151": ("ja", "SV2a"),
    "Neo Genesis (Japanese)": ("ja", "neo1"),
    "Neo Discovery (Japanese)": ("ja", "neo2"),
    "VMAX Rising": ("ja", "S1a"),
}

# Sets CollectR reports that have no checklist to diff against. Sealed product
# and CollectR's own catch-all bucket live here too.
UNTRACKED = {
    "Miscellaneous Cards & Products",
    "McDonald's Promo (2025)",
    "First Partner Collection 2026",
    "SVG Special Deck Set ex",
}

# CollectR finish label -> TCGdex variant key
FINISH = {
    "normal": "normal",
    "unlimited": "normal",
    "reverse holofoil": "reverse",
    "holofoil": "holo",
    "unlimited holofoil": "holo",
    "1st edition": "firstEdition",
}
VARIANT_LABEL = {
    "normal": "Normal",
    "reverse": "Reverse",
    "holo": "Holo",
    "firstEdition": "1st Ed",
}

# TCGdex marks a 1st Edition variant on every WOTC-era card, which would make
# master-set mode demand a 1st Ed copy of all of Base Set. Opt in with --first-ed.
ALL_VARIANTS = ("normal", "reverse", "holo", "firstEdition")

# Some sets have no reverse-holo data filed upstream yet. Where that happens we
# reconstruct the run: modern sets print a reverse of every Common, Uncommon and
# Rare inside the numbered run, and none of the Double rares, ACE SPECs or
# secrets above it. Verified against sets that do carry the data.
REVERSE_RARITIES = {"Common", "Uncommon", "Rare"}
# Reverse holos start with Legendary Collection (May 2002); nothing earlier has
# them, and Japanese sets don't print a parallel reverse run at all.
REVERSE_ERA = "2002-05-01"


CURRENCIES = {"GBP": "\u00a3", "EUR": "\u20ac", "USD": "$"}
SYMBOL_TO_CODE = {"\u00a3": "GBP", "\u20ac": "EUR", "$": "USD", "\u00a5": "JPY"}


def parse_money(v):
    """'£314.46' -> ('GBP', 314.46). Returns (None, None) if unparseable."""
    t = str(v or "").strip()
    m = re.match(r"^\s*([\u00a3\u20ac$\u00a5]?)\s*([\d,]+\.?\d*)\s*$", t)
    if not m:
        return None, None
    code = SYMBOL_TO_CODE.get(m.group(1))
    try:
        return code, float(m.group(2).replace(",", ""))
    except ValueError:
        return None, None


def fx_rate(to):
    """EUR -> target rate from the ECB via Frankfurter. Cached like everything else."""
    if to == "EUR":
        return 1.0, "n/a"
    d = fetch_url(f"https://api.frankfurter.dev/v1/latest?base=EUR&symbols={to}")
    if not d or to not in (d.get("rates") or {}):
        return None, None
    return d["rates"][to], d.get("date", "")


def card_price(card, rate):
    """Cardmarket trend price for the plain and foil printings, converted.

    Cardmarket is the European market, so it's the relevant one for a UK
    collector - TCGplayer is US retail in dollars. 'trend' is Cardmarket's own
    smoothed figure; avg30 and avg are fallbacks when it's missing.
    """
    cm = ((card or {}).get("pricing") or {}).get("cardmarket") or {}
    if not cm:
        return None

    def pick(suffix):
        for k in ("trend", "avg30", "avg"):
            v = cm.get(k + suffix)
            if isinstance(v, (int, float)) and v > 0:
                return round(v * rate, 2)
        return None

    plain, foil = pick(""), pick("-holo")
    return [plain, foil] if (plain or foil) else None


def norm_name(v):
    """'Base Set (Unlimited)' -> 'base set';  'Scarlet & Violet' -> 'scarlet violet'."""
    t = re.sub(r"\(.*?\)", " ", str(v).lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def fetch_url(url):
    """Cached GET for a full URL (used for things outside the TCGdex API)."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, "url_" + re.sub(r"[^A-Za-z0-9._-]", "_", url) + ".json")
    if os.path.exists(key):
        with open(key) as fh:
            return json.load(fh)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "binder-report/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        data = None
    with open(key, "w") as fh:
        json.dump(data, fh)
    return data


def fetch(path):
    """GET a TCGdex path, caching the JSON on disk."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9._-]", "_", path) + ".json")
    if os.path.exists(key):
        with open(key) as fh:
            return json.load(fh)
    req = urllib.request.Request(
        f"{API}/{path}", headers={"User-Agent": "binder-report/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError):
        data = None
    with open(key, "w") as fh:
        json.dump(data, fh)
    return data


def embed_art(sets, only_owned=True):
    """Inline card art as data URIs so the page works with no connection."""
    import base64

    targets = []
    for st in sets:
        for c in st["cards"]:
            if c["img"] and (c["qty"] > 0 or not only_owned):
                targets.append(c)
    print(f"embedding {len(targets)} images", file=sys.stderr)

    def grab(c):
        url = c["img"] + "/low.webp"
        key = os.path.join(CACHE, "img_" + re.sub(r"[^A-Za-z0-9]", "_", url))
        if os.path.exists(key):
            return c, open(key, "rb").read()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "binder-report/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                blob = r.read()
        except Exception:
            blob = b""
        with open(key, "wb") as fh:
            fh.write(blob)
        return c, blob

    total = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        for c, blob in pool.map(grab, targets):
            if not blob:
                c["img"] = None
                continue
            c["img"] = "data:image/webp;base64," + base64.b64encode(blob).decode()
            c["inline"] = True
            total += len(blob)
    # Anything not embedded would still hit the network; drop it for consistency.
    for st in sets:
        for c in st["cards"]:
            if not c.get("inline"):
                c["img"] = None
            else:
                del c["inline"]
    print(f"embedded {total/1048576:.1f} MB of art", file=sys.stderr)


def clean_card_name(name):
    """'Eevee (Pokemon Day 2025) (Reverse Cosmos Holo)' -> 'Eevee'."""
    return re.sub(r"\s*[\(\[].*", "", str(name)).strip()


def set_names():
    """{tcgdex set id: display name}."""
    m = {}
    for lang in ("en", "ja"):
        for st in fetch(f"{lang}/sets") or []:
            m[st["id"]] = st["name"]
    return m


def set_sizes():
    """{tcgdex set id: {official count, total count}} - used to disambiguate."""
    m = {}
    for lang in ("en", "ja"):
        for st in fetch(f"{lang}/sets") or []:
            cc = st.get("cardCount") or {}
            m[st["id"]] = {cc.get("official"), cc.get("total")} - {None}
    return m


def find_card(name, number, sizes):
    """Look a stray card up by name across every set.

    CollectR files some cards under catch-all buckets - a Prismatic Evolutions
    Eevee lands in 'Miscellaneous Cards & Products'. Searching by name recovers
    the real card and its art.

    Card numbers alone are far too weak to match on: 120 different cards are
    called Pikachu, so 'number 20' hits a dozen sets. Two ways in, both of
    which have to be unambiguous or we return nothing:

      1. the name is unique across TCGdex (only one Ancient Mew exists), or
      2. the '/131' denominator in the CollectR number matches exactly one
         candidate set's card count.
    """
    hits = fetch(f"en/cards?name=eq:{urllib.parse.quote(clean_card_name(name))}")
    if not isinstance(hits, list) or not hits:
        return None

    if len(hits) == 1:
        return hits[0] if hits[0].get("image") else None

    m = re.match(r"^\s*\d+\s*/\s*(\d+)\s*$", str(number or ""))
    if not m:
        return None
    denom, want = int(m.group(1)), norm_num(number)

    cand = [
        c
        for c in hits
        if norm_num(c.get("localId")) == want
        and c.get("image")
        and denom in sizes.get(c["id"].rsplit("-", 1)[0], set())
    ]
    return cand[0] if len(cand) == 1 else None


def convert(amount, frm, to):
    """Convert between currencies via the ECB, or return as-is when they match."""
    if amount is None or not frm:
        return None
    if frm == to:
        return round(amount, 2)
    d = fetch_url(f"https://api.frankfurter.dev/v1/latest?base={frm}&symbols={to}")
    r = ((d or {}).get("rates") or {}).get(to)
    return round(amount * r, 2) if r else None


def build_loose(loose, sets, sizes, names, currency=None):
    """Sealed product, and cards from sets with no checklist to diff against.

    Neither can be 'missing' from anything, so these are shown as a plain
    inventory rather than a checklist - but nothing in the CSV goes unshown.
    """
    logos = {st["label"]: st.get("logo") for st in sets}
    sealed, orphan = [], []
    for r in loose:
        entry = {
            "name": r["name"],
            "set": r["set"],
            "qty": r["qty"],
            "logo": logos.get(r["set"]),
        }
        # TCGdex has no sealed-product data at all, so CollectR's own valuation
        # is the only price available for these.
        if currency and r.get("amt") is not None:
            v = convert(r["amt"], r["cur"], currency)
            if v:
                entry["p"] = v
        if r["n"]:
            entry["n"] = r["n"]
            entry["finish"] = r["finish"]
            hit = find_card(r["name"], r["n"], sizes)
            if hit:
                sid = hit["id"].rsplit("-", 1)[0]
                entry["img"] = hit["image"]
                entry["found"] = names.get(sid, sid)
            orphan.append(entry)
        else:
            sealed.append(entry)
    sealed.sort(key=lambda e: (e["set"], e["name"]))
    orphan.sort(key=lambda e: (e["set"], e["name"]))
    return sealed, orphan


def norm_num(v):
    """'238/191' -> '238';  '051/049' -> '51';  '025' -> '25';  'TG01' -> 'tg1'."""
    if v is None:
        return ""
    s = str(v).strip().split("/")[0].strip().lower()
    m = re.match(r"^([a-z]*)0*(\d+)$", s)
    return f"{m.group(1)}{m.group(2)}" if m else s


def set_index():
    """Every TCGdex set, keyed by normalised name. Two API calls, then cached."""
    idx = {"en": {}, "ja": {}}
    for lang in ("en", "ja"):
        for st in fetch(f"{lang}/sets") or []:
            idx[lang].setdefault(norm_name(st["name"]), (lang, st["id"], st["name"]))
    return idx


def resolve_sets(labels, idx):
    """CollectR set names -> (lang, tcgdex id). Overrides win; then exact name."""
    resolved, auto, unknown = {}, [], []
    for lab in sorted(labels):
        if lab in UNTRACKED:
            continue
        if lab in SET_OVERRIDES:
            resolved[lab] = SET_OVERRIDES[lab]
            continue
        # CollectR flags Japanese printings in the set name itself.
        order = ("ja", "en") if re.search(r"japanese|\bjp\b", lab, re.I) else ("en", "ja")
        hit = next((idx[l].get(norm_name(lab)) for l in order if idx[l].get(norm_name(lab))), None)
        if hit:
            resolved[lab] = (hit[0], hit[1])
            auto.append((lab, hit[0], hit[1], hit[2]))
        else:
            near = difflib.get_close_matches(
                norm_name(lab), list(idx["en"]) + list(idx["ja"]), 3, 0.7
            )
            unknown.append((lab, near))
    return resolved, auto, unknown


def read_collection(path, resolved):
    """CSV -> owned dict and rows with no checklist to diff against."""
    import csv

    owned, loose = {}, []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            cset = (row.get("set") or "").strip()
            num = norm_num(row.get("number"))
            qty = int(row.get("qty") or 1)
            fin = (row.get("finish") or "").strip()
            key = FINISH.get(fin.lower()) or "normal"

            # Sealed product, CollectR's catch-all bucket, and sets TCGdex
            # doesn't carry: no checklist to diff, but still tradeable.
            if not num or cset not in resolved:
                cur, amt = parse_money(row.get("value"))
                loose.append(
                    {
                        "set": cset,
                        "n": row.get("number") or "",
                        "name": row["name"],
                        "finish": fin,
                        "qty": qty,
                        "cur": cur,
                        "amt": amt,
                    }
                )
                continue

            e = owned.setdefault(
                (cset, num), {"qty": 0, "finishes": {}, "name": row["name"]}
            )
            e["qty"] += qty
            e["finishes"][key] = e["finishes"].get(key, 0) + qty
    return owned, loose


def build_sets(owned, resolved, want_variants, keep_variants, rate=None):
    """Assemble per-set checklists, marking which cards and variants are owned."""
    used = sorted({c for c, _ in owned})
    out = []

    for cset in used:
        lang, sid = resolved[cset]
        meta = fetch(f"{lang}/sets/{sid}")
        if not meta:
            print(f"  ! could not load {cset} ({lang}/{sid})", file=sys.stderr)
            continue
        cards = meta.get("cards") or []

        variants, by_dex, rarity, prices = {}, {}, {}, {}
        inferred_rev = False
        if want_variants:
            print(f"  detail: {cset} ({len(cards)} cards)", file=sys.stderr)
            with ThreadPoolExecutor(max_workers=12) as pool:
                full = list(pool.map(lambda c: fetch(f"{lang}/cards/{c['id']}"), cards))
            for c, d in zip(cards, full):
                v = (d or {}).get("variants") or {}
                variants[c["id"]] = [k for k in keep_variants if v.get(k)]
                rarity[c["id"]] = (d or {}).get("rarity")
                if rate is not None:
                    pr = card_price(d, rate)
                    if pr:
                        prices[c["id"]] = pr
                # Some CollectR entries (notably Japanese sets) carry the
                # national Pokedex number instead of the set number.
                for dex in (d or {}).get("dexId") or []:
                    by_dex.setdefault(str(dex), c["id"])

        official = (meta.get("cardCount") or {}).get("official") or 0
        if (
            want_variants
            and "reverse" in keep_variants
            and official
            and lang == "en"
            and (meta.get("releaseDate") or "") >= REVERSE_ERA
            and not any("reverse" in v for v in variants.values())
        ):
            for c in cards:
                digits = re.sub(r"\D", "", str(c.get("localId") or ""))
                if (
                    digits
                    and int(digits) <= official
                    and rarity.get(c["id"]) in REVERSE_RARITIES
                ):
                    variants[c["id"]].append("reverse")
                    inferred_rev = True

        claimed = set()
        rows = []
        for c in cards:
            n = norm_num(c.get("localId"))
            have = owned.get((cset, n))
            if have:
                claimed.add(n)
            rows.append(
                {
                    "id": c["id"],
                    "n": c.get("localId"),
                    "name": c.get("name"),
                    "img": c.get("image"),
                    "qty": have["qty"] if have else 0,
                    "has": dict(sorted(have["finishes"].items())) if have else {},
                    "vars": variants.get(c["id"], []),
                    "p": prices.get(c["id"]),
                }
            )

        # Second pass: anything still unmatched, try it as a Pokedex number.
        row_by_id = {r["id"]: r for r in rows}
        for (s, n), v in owned.items():
            if s != cset or n in claimed:
                continue
            hit = row_by_id.get(by_dex.get(n.lstrip("0") or "0", ""))
            if hit and hit["qty"] == 0:
                hit["qty"] = v["qty"]
                hit["has"] = dict(sorted(v["finishes"].items()))
                claimed.add(n)

        strays = [
            {"n": n, "name": v["name"], "qty": v["qty"]}
            for (s, n), v in owned.items()
            if s == cset and n not in claimed
        ]
        for r in rows:
            del r["id"]

        out.append(
            {
                "label": cset,
                "tcgdex": f"{lang}/{sid}",
                "logo": meta.get("logo"),
                "revInferred": inferred_rev,
                "released": meta.get("releaseDate"),
                "cards": rows,
                "strays": sorted(strays, key=lambda r: norm_num(r["n"])),
            }
        )

    out.sort(key=lambda s: (s.get("released") or ""), reverse=True)
    return out



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("-o", "--out", default="binder.html")
    ap.add_argument("--owner", default="", help="name shown in the header")
    ap.add_argument(
        "--no-variants",
        action="store_true",
        help="skip per-card detail lookups (faster, disables master-set mode)",
    )
    ap.add_argument(
        "--currency",
        default="GBP",
        choices=sorted(CURRENCIES),
        help="convert Cardmarket's EUR prices to this currency (default GBP)",
    )
    ap.add_argument(
        "--no-prices",
        action="store_true",
        help="skip pricing entirely",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cache and re-fetch everything (picks up newly added "
        "sets, cards and variant data upstream)",
    )
    ap.add_argument(
        "--sets",
        default="",
        help="comma-separated CollectR set names to include (default: all)",
    )
    ap.add_argument(
        "--embed-art",
        action="store_true",
        help="inline card art so the file works offline (much larger; "
        "pair with --sets to keep it sendable)",
    )
    ap.add_argument(
        "--all-art",
        action="store_true",
        help="with --embed-art, also inline art for cards you don't own",
    )
    ap.add_argument(
        "--first-ed",
        action="store_true",
        help="count 1st Edition as a required variant in master-set mode",
    )
    args = ap.parse_args()
    keep = ALL_VARIANTS if args.first_ed else tuple(
        v for v in ALL_VARIANTS if v != "firstEdition"
    )

    if args.refresh and os.path.isdir(CACHE):
        import shutil

        shutil.rmtree(CACHE)
        print("cache cleared")

    import csv as _csv

    with open(args.csv, newline="", encoding="utf-8-sig") as fh:
        labels = {(r.get("set") or "").strip() for r in _csv.DictReader(fh)}
    resolved, auto, unknown = resolve_sets(labels, set_index())

    if auto:
        print("matched automatically:")
        for lab, lang, sid, name in auto:
            print(f"  {lab}  ->  {lang}/{sid}  ({name})")
    if unknown:
        print("\nNo TCGdex match — add to SET_OVERRIDES, or UNTRACKED to skip:")
        for lab, near in unknown:
            hint = f"   closest: {', '.join(near)}" if near else ""
            print(f"  {lab!r}{hint}")

    empty = [
        lab
        for lab, (lang, sid) in resolved.items()
        if not ((fetch(f"{lang}/sets/{sid}") or {}).get("cards"))
    ]
    for lab in empty:
        print(f"  ! {lab} has no cards filed in TCGdex yet - leaving untracked")
        del resolved[lab]

    owned, loose = read_collection(args.csv, resolved)
    print(f"\n{len(owned)} distinct cards across {len({c for c,_ in owned})} sets")
    if loose:
        print(f"{len(loose)} rows with no checklist (sealed, promos, untracked sets)")

    rate, fx_date = (None, None)
    if not args.no_prices and not args.no_variants:
        rate, fx_date = fx_rate(args.currency)
        if rate is None:
            print(f"  ! could not get an EUR->{args.currency} rate - prices off")
        else:
            print(f"prices in {args.currency} at {rate:.4f}/EUR (ECB {fx_date})")

    sets = build_sets(owned, resolved, not args.no_variants, keep, rate)

    if args.sets:
        wanted = {x.strip().lower() for x in args.sets.split(",") if x.strip()}
        missing = wanted - {s["label"].lower() for s in sets}
        for m in sorted(missing):
            print(f"  ! no set named {m!r}", file=sys.stderr)
        sets = [s for s in sets if s["label"].lower() in wanted]
        if not sets:
            sys.exit("nothing to build - check --sets against the names above")

    sealed, orphan = build_loose(
        loose, sets, set_sizes(), set_names(), args.currency if rate else None
    )
    if sealed:
        print(f"{sum(e['qty'] for e in sealed)} sealed items, "
              f"{sum(e['qty'] for e in orphan)} cards outside a tracked set")

    if args.embed_art:
        embed_art(sets, only_owned=not args.all_art)

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "binder_template.html"), encoding="utf-8") as fh:
        tpl = fh.read()

    payload = {
        "owner": args.owner,
        "sets": sets,
        "sealed": sealed,
        "orphan": orphan,
        "variantData": not args.no_variants,
        "currency": args.currency if rate else None,
        "symbol": CURRENCIES.get(args.currency, ""),
        "fxDate": fx_date,
        "built": __import__("datetime").date.today().isoformat(),
        "offlineArt": args.embed_art,
        "labels": VARIANT_LABEL,
    }
    held = sum(1 for st in sets for c in st["cards"] if c["qty"] > 0)
    total = sum(len(st["cards"]) for st in sets)
    who = (args.owner + "'s") if args.owner else "My"
    title = f"{who} Pok\u00e9mon set checklist"
    desc = (
        f"{held} of {total} cards across {len(sets)} sets \u2014 "
        "see what's still missing."
    )
    tpl = tpl.replace("__TITLE__", title).replace("__DESC__", desc)

    html = tpl.replace(
        "/*__DATA__*/null",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"\nwrote {args.out} ({os.path.getsize(args.out)/1024:.0f} KB)")


if __name__ == "__main__":
    main()