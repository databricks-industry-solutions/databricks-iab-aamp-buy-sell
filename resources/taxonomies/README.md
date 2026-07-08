# Vendored IAB taxonomies — the single source of truth for IAB codes

This is **where IAB taxonomy codes are managed** for the whole accelerator. Drop the
sha-locked TSVs here (filenames matter):

```
resources/taxonomies/
  Audience Taxonomy 1.1.tsv
  Content Taxonomy 3.1.tsv
```

They come from the IAB SDK (`<sdk>/data/taxonomies/…`, CC-BY). Fetch them with:
```bash
make taxonomies      # copies from a local clone of the seller-agent SDK (set SDK_DIR)
```
The TSVs are **git-ignored** (licensed / vendored per deployment), so a clone runs
`make taxonomies` once.

## Who uses them (one vocabulary, everywhere)
- **`buyer/`** — the L3 `audience_planner` proposes codes from a brief's free-text
  interests; **`TaxonomyLookupTool`** validates each against these TSVs so the LLM
  cannot invent a code.
- **`seed/`** — the validator does an **advisory** check that every theme audience
  code exists here (warns if not — see below).
- **`seller/`** — packages declare `standard_segment_ids` from this same vocabulary,
  so a resolved brief code lands on a code the seller actually offers (§8.1 coherence).

## Two levels of "managed"
- **Authoritative catalog of valid codes** → these TSVs (centralized).
- **Which subset a vertical uses** → the theme YAMLs (`seed/themes/*.yaml`,
  `audiences[].iab`).

> ✅ Every theme audience `iab` is a **real Audience Taxonomy 1.1 Unique ID** (integers
> 1–1559, e.g. `607` = Interest \| Sports, `1404` = Purchase Intent \| Food and Beverage
> Services \| Fast Food). Two verticals have no exact leaf in Taxonomy 1.1 and map to the
> nearest real segment (auto "EV & Auto Tech" → `246` Auto Technology; finance "Mortgage
> Shoppers" → `1394` Mortgage Lenders and Brokers). The seed validator prints a `⚠ NOTE`
> for any code not found in the vendored TSV (advisory — it does not fail the build).
