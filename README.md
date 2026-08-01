# Pokémon binder

Turns a CollectR collection export into a shareable set-completion page:
what you have, what's missing, base prints paired with their reverse holos.

```
collectr-export-v2.js  ->  paste in the browser console on your CollectR
                           showcase page, exports collectr-export.csv
build_binder.py        ->  CSV + TCGdex checklists -> a single HTML file
binder_template.html   ->  the page itself (styles + behaviour)
publish.sh             ->  rebuild and push to GitHub Pages (macOS/Linux)
publish.ps1            ->  the same, for Windows PowerShell
```

## Build locally

macOS / Linux:

```bash
python3 build_binder.py collectr-export.csv -o index.html --owner "David"
open index.html
```

Windows PowerShell:

```powershell
python build_binder.py collectr-export.csv -o index.html --owner "David"
start index.html
```

TCGdex responses cache in `.tcgdex_cache/`, so the first run takes a few
minutes and every run after that takes seconds. Delete the folder to pick up
newly released sets.

## Publish to GitHub Pages

One-time setup:

```bash
# 1. make a repo (public - Pages needs it on the free plan)
mkdir pokemon-binder && cd pokemon-binder
git init -b main
echo "<!-- placeholder -->" > index.html
git add . && git commit -m "init"
git remote add origin git@github.com:YOURNAME/pokemon-binder.git
git push -u origin main

# 2. on github.com: Settings -> Pages
#    Source: "Deploy from a branch", Branch: main, Folder: / (root)
```

Then every update is one command:

macOS / Linux:

```bash
export BINDER_OWNER="David"
./publish.sh collectr-export.csv .
```

Windows PowerShell:

```powershell
$env:BINDER_OWNER = "David"
.\publish.ps1 collectr-export.csv .
```

If PowerShell refuses to run the script, allow local scripts once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

On Windows use `python` rather than `python3` when calling the build script
directly — `python3` is usually a Microsoft Store stub that opens the Store.

The page lands at `https://YOURNAME.github.io/pokemon-binder/`.

Note that a Pages site is public — anyone with the link can see the
checklist. It shows card names and completion only, no values and no
personal details, but it is a public inventory of what you own.

## Options

| Flag | What it does |
| --- | --- |
| `--owner NAME` | Name in the header and the link preview |
| `--sets "A,B"` | Only include the named CollectR sets |
| `--embed-art` | Inline the art so the file works offline (large — pair with `--sets`) |
| `--all-art` | With `--embed-art`, also inline cards you don't own |
| `--first-ed` | Count 1st Edition as a required variant |
| `--no-variants` | Skip per-card detail lookups; disables the split view |

## New sets

Usually nothing to do. The build caches TCGdex's full set index (two API
calls, ~400 sets) and resolves CollectR set names against it by exact name,
printing what it matched so you can sanity-check.

Only names that can't match on their own need a hand: promo sets TCGdex files
under a different name, and Japanese printings it lists under Japanese names.
Those go in `SET_OVERRIDES` as `"CollectR name": ("en", "tcgdex-id")`, with
IDs from `https://api.tcgdex.net/v2/en/sets` (or `/ja/sets`). The build prints
the closest names it knows when it can't match one.

Sets with no checklist to diff against go in `UNTRACKED` instead.

Matching is exact, never fuzzy — a wrong set silently attached to your cards
is worse than an unmatched one you get told about.

## Refreshing stale data

`.tcgdex_cache/` never expires. If a set gains cards or variant data upstream
you won't see it until you clear it:

```
python build_binder.py collectr-export.csv -o index.html --refresh
```

That re-fetches everything and takes a few minutes. Worth doing every few
months, or when a reconstructed reverse run gets flagged on the page.

## Known data gaps

- Some recent sets have no reverse-holo data in TCGdex. Those runs are
  reconstructed from rarity (every Common, Uncommon and Rare inside the
  numbered run) and labelled as inferred on the page.
- Mega Evolution Promos is missing numbers 046–063 upstream, so cards in
  that range show under "Not on this checklist".
- The CollectR showcase export carries no purchase price and no grades, so
  neither appears here.
