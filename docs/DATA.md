# Preparing the data

JockeyBench reads JRDB member data: fixed-width Shift-JIS archives published to subscribers. **Nothing
from JRDB is distributed with this repository.** That covers archives, specification documents,
parsed rows, databases, pseudonymised records, keys and any run output derived from them. Each user
subscribes independently at [jrdb.com](https://jrdb.com/order), reads JRDB's terms, and keeps
everything below on their own machine. Pseudonymisation does not make licensed data public. Never
attach outputs to issues, pull requests or CI artifacts.

Every `data` command except `layouts` runs only on a designated experiment host (macOS or Linux,
including [Windows through WSL2](WINDOWS.md)),
from the checkout root already provisioned with the `.experiment-host` marker. Do not create or
copy that marker onto a development machine; see [Running](../README.md#run). Linux installation
requires the system packages listed in [Install](../README.md#install).

## 1. Place the archives

Download with your own account and JRDB's supported process; the importer has no downloader.

```text
data/
  raw/                      # archives exactly as downloaded
    Bac/BAC_2024.lzh        # yearly packs or daily files (BAC240106.zip); both are supported
    Kyi/...  Sed/...  ...
  docs/                     # JRDB specification documents, as downloaded
    Bac/bac_doc.txt
    ...
  private/                  # built databases and the namespace key (mode 700)
```

The importer reads `.zip` archives with Python's `zipfile` and `.lzh` archives with `bsdtar` (part of
macOS; provided by `libarchive-tools` on Ubuntu), falling back to `lhafile` (in the lock).
Installing `lhafile` may compile a C extension on Linux, especially on ARM; the setup instructions
include `build-essential` and explain the matching Python development headers requirement.
`jockeybench data layouts` prints the directory and specification document for every record type.
It reads no data. The importer uses BAC, KAB, KYI, UKC, KKA, CYB, CHA, JOA, TYB, SED, SKB, SRB and HJC.
Cards need at least BAC and KYI. Missing types lower coverage and are listed in the build receipt.
Layouts from 2015 onward are supported. For the paper protocol, cover the history the agents may
query plus the 2024 evaluation window. **2026 data are never imported or read.**

## 2. Record provenance

```bash
.venv/bin/jockeybench data manifest --raw data/raw --start 2015-01-01 --end 2024-12-31 --fetched-at "$JRDB_FETCHED_AT"
```

Set `JRDB_FETCHED_AT` to the time you downloaded this batch, with its timezone (for example
`2025-03-01T12:00:00+09:00`). The command hashes every archive of the selected years into
`data/raw/manifest.jsonl`, with that time. It never invents a publication time. It refuses to overwrite an existing
manifest, and `build` refuses an archive whose hash does not match.

## 3. Build and key the database

```bash
.venv/bin/jockeybench data build --raw data/raw --docs data/docs --database data/private/built.sqlite --start 2015-01-01 --end 2024-12-31
.venv/bin/jockeybench data anonymize --source data/private/built.sqlite --output data/private/expanded.sqlite --key data/private/namespace.key
```

Both commands write only to fresh paths with private permissions and create missing directories.

* `build` decodes the archives into `races` and `entries`, one row per race and runner. Every value
  passes through a reviewed field whitelist (`importer/fields.py`). The command also writes
  `sources` (provenance of every decoded member) and `metadata`. It leaves a receipt next to the
  database (`*.build.json`), with counts and missing record types per day.
* `anonymize` copies the database, generates (or reuses) a 32-byte namespace key, keys every race
  token with HMAC-SHA256 and stores the key in the copy's metadata. Registration numbers and jockey
  and trainer codes stay raw in the database. The runtime derives horse, jockey, trainer and pedigree
  tokens with the key when it serves data, so names and registration numbers never reach an agent.
  Treat `expanded.sqlite` exactly like the raw archive. Its name and the `expanded_race_ids` table are
  kept so that databases built for the paper still load.

The private database keeps more than agents can see, such as odds, payouts and free-text comments.
The runtime's projection, not the database, is the information boundary. The evaluator uses closing
odds and payouts only for private diagnostics.

Pseudonyms depend on your key. Programs that hash identifier strings, such as reference program R3
on pedigree tokens, therefore compute slightly different features under a different key. Archive
timing is a historical reconstruction: original publication and revision times are generally
unknown, and receipts say so.
