# CRIF PDF Engine

A reusable, CRIF Highmark-specific PDF generation engine built on
[ReportLab](https://www.reportlab.com/): parses a raw CRIF Highmark API
JSON response into a normalized internal data model and renders it as a
paginated credit report PDF, matching the layout of
[`docs/sample_report.pdf`](docs/sample_report.pdf).

The engine (`pdf_engine/`) has no dependency on any web framework. An
optional Django integration layer (`services/crif_pdf_service.py`) is
included for projects that want to save generated reports under
`MEDIA_ROOT`.

## Installation

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

For running the test suite, install the dev extras instead (this
transitively installs `requirements.txt`):

```bash
pip install -r requirements-dev.txt
```

The package also declares standard packaging metadata
(`pyproject.toml`), so it can be installed as a normal (editable or
regular) Python package from the repo root:

```bash
pip install -e .          # editable install, for local development
pip install .             # regular install
pip install ".[dev]"      # regular install + test dependencies
```

After installation, `import pdf_engine` works from anywhere, not just
from within this repo's directory.

## Project structure

```
pdf_engine/            # The engine itself -- framework-agnostic
    __init__.py         # Public API re-exports
    constants.py         # Page geometry, spacing/typography scale, asset paths
    theme.py              # Font registration + color palette
    styles.py              # ReportLab ParagraphStyle / TableStyle definitions
    helpers.py              # Generic flowable-building utilities
    parser.py                # Raw CRIF JSON -> normalized CreditReport model
    generator.py               # Orchestrates sections into a story, renders the PDF
    sections/                   # One module per report section (see below)

services/                # Optional Django integration
    crif_pdf_service.py   # generate_crif_pdf(): saves under MEDIA_ROOT/crif_reports/

tests/                  # pytest suite for parser.py and generator.py
assets/                 # Fonts (Montserrat/Poppins/Roboto) + CRIF logo
docs/sample_report.pdf  # Reference report this engine's layout matches
input/crif_response.json  # Sample raw CRIF Highmark API payload
output/                 # Default local output directory (gitignored PDFs)
```

Each module under `pdf_engine/sections/` renders exactly one section of
the report (masthead, identity, score, score trend, account/personal-info
summaries, employment, per-account details, per-account payment history,
inquiries, appendix/footer) via a single `render(story, report)` entry
point, and is independently responsible for skipping itself when it has
nothing to render. `pdf_engine/generator.py` calls each of them once, in
report order -- see that module's docstring for the exact section
sequence and why account details and payment history are *not*
interleaved per account.

## `generate_report()`

This is the single entry point most callers need: parse a raw bureau
response straight to a saved PDF.

```python
import json
from pdf_engine import generate_report

with open("input/crif_response.json", encoding="utf-8") as f:
    raw_json = json.load(f)

pdf_path = generate_report(raw_json, "output/report.pdf")
print(pdf_path)  # -> resolved Path the PDF was written to
```

`raw_json` is the full decoded CRIF Highmark API response body (the
`data.result_json.credit_report` payload shape parsed by
`pdf_engine.parser.parse_credit_report`). `output_path` accepts a `str`
or `pathlib.Path`; its parent directory is created automatically if it
doesn't already exist.

For lower-level control -- e.g. inspecting the parsed data before
rendering, or reusing an already-parsed `CreditReport` -- use the two
halves of the pipeline directly:

```python
from pdf_engine import parse_credit_report, build_story, render_pdf

report = parse_credit_report(raw_json)   # normalized CreditReport
print(report.customer_identity.name, len(report.accounts))

story = build_story(report)              # list of ReportLab flowables
render_pdf(story, "output/report.pdf")
```

`parse_credit_report` never raises for malformed or missing input --
every field degrades to a safe default (`None` / `""` / `[]`) and logs a
warning/debug message describing what was missing, so a best-effort
report is always produced. Configure Python's standard `logging` module
in your application if you want those messages surfaced (see
[Logging](#logging) below).

## Django integration

`services/crif_pdf_service.py` wraps `pdf_engine.generate_report` for a
Django project: it resolves an output path under
`MEDIA_ROOT/crif_reports/`, generates a UUID-named PDF there, and
returns where it landed.

```python
from services.crif_pdf_service import generate_crif_pdf

result = generate_crif_pdf(raw_json)
# {
#     "pdf_path": "/absolute/path/to/MEDIA_ROOT/crif_reports/<uuid>.pdf",
#     "filename": "<uuid>.pdf",
#     "size": 110066,
# }
```

Requires `settings.MEDIA_ROOT` to be configured; raises
`django.core.exceptions.ImproperlyConfigured` if it isn't. This module is
the only place in the repo that imports Django -- `pdf_engine` itself has
no Django dependency, so non-Django callers should install only
`reportlab` (see [Installation](#installation)) and call
`pdf_engine.generate_report` directly instead.

## Asset directory

`pdf_engine` does not bundle its own copy of fonts or the logo. By
default it reads from this repository's top-level `assets/` directory (a
sibling of the `pdf_engine` package directory):

```
assets/
    criflogo.png
    Montserrat/...
    Poppins/...
    Roboto/...
```

That sibling-directory layout only holds while `pdf_engine/` stays in
this exact position relative to `assets/`. If you install `pdf_engine`
as a package into a different project (e.g. via `pip install .` from
this repo, as described above) and copy `assets/` somewhere else, point
the engine at it with the `PDF_ENGINE_ASSETS_DIR` environment variable:

```bash
export PDF_ENGINE_ASSETS_DIR=/path/to/assets
```

Read once at import time (`pdf_engine/constants.py`). A missing or
unreadable font falls back to a built-in Helvetica variant, and a
missing logo is simply omitted -- both are logged as warnings, neither
is fatal.

## Output directory

`output/` is this repo's default local scratch directory for generated
PDFs (used by the test suite's temporary paths and as a convenient
target for ad hoc local runs). Generated PDFs are build output, not
source, so `*.pdf` is gitignored repo-wide (with `docs/sample_report.pdf`
explicitly excepted, since that one is a checked-in reference asset, not
generated output). `render_pdf`/`generate_report` create any missing
parent directories for you, so `output/` itself doesn't need to exist
ahead of time.

In a Django deployment, generated reports instead land under
`MEDIA_ROOT/crif_reports/` (see [Django integration](#django-integration)
above), not under this repo's `output/` directory.

## Logging

Every module logs via the standard `logging` module (`logger =
logging.getLogger(__name__)`) rather than raising for recoverable
problems -- malformed dates, unexpected payload shapes, missing font/logo
files, etc. Nothing in `pdf_engine` configures a logging handler itself,
so by default these messages go to Python's "handler of last resort"
(stderr, level `WARNING` and above). To capture them properly, configure
logging in your own application, e.g.:

```python
import logging

logging.basicConfig(level=logging.INFO)
```

or, in a Django project, via `settings.LOGGING`.

## Adding new bureaus

This engine is currently CRIF Highmark-specific end to end: the parser
knows CRIF's exact JSON shape and micro-formats (pipe-delimited score
factors, `combined_payment_history` tokens, etc.), and the section
modules render CRIF's specific report layout. Supporting a second bureau
means adding a parallel pipeline, not branching the existing one:

1. **New parser module** (e.g. `pdf_engine/other_bureau_parser.py`)
   producing the *same* normalized model as `pdf_engine/parser.py`
   (`CreditReport` and its nested dataclasses) from that bureau's raw
   payload shape. Reusing the existing model means every existing
   section module keeps working unchanged, as long as the new parser can
   populate it faithfully.
   - If the other bureau reports data CRIF doesn't (or vice versa),
     extend the dataclasses in `pdf_engine/parser.py` with new
     `= default` fields rather than creating bureau-specific variants --
     existing callers and section modules are unaffected by an added
     field with a safe default.
2. **New entry point** mirroring `generate_report`/`parse_credit_report`
   (e.g. `generate_other_bureau_report`), or a `source` parameter that
   selects which parser to run, depending on how your integration needs
   to dispatch between bureaus.
3. **Section-level differences**, if the new bureau's report layout
   genuinely differs from CRIF's (not just its raw data shape): add a
   bureau-specific section module under `pdf_engine/sections/` and select
   the right module set in a small wrapper around
   `pdf_engine.generator.build_story`, rather than adding bureau branches
   inside the existing CRIF section modules.
4. Reuse `pdf_engine/theme.py`, `styles.py`, `constants.py`, and
   `helpers.py` as-is -- none of them are CRIF-specific; they are the
   presentation foundation every bureau's report should share.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers `pdf_engine/parser.py` (primitive coercion helpers, full
parses of the real sample payload, and defensive handling of malformed
input) and `pdf_engine/generator.py` (story assembly and end-to-end PDF
generation, validated with [`pypdf`](https://pypi.org/project/pypdf/)).
