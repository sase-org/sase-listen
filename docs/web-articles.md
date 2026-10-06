# Web articles

`script` and `render` accept public article URLs. Pages are fetched on this
machine with a browser-like HTTP client, then extracted locally; no hosted
reader receives the URL or page text.

```bash
sase-listen script https://example.com/article --json
sase-listen render https://example.com/article --dry-run
```

The first request stores the original HTML, repaired Markdown, extraction
metadata, and deterministic verbatim script under
`$XDG_DATA_HOME/sase-listen/sources/`. Later requests reuse that source, so a
render can run offline and uses the same script. Pass `--refresh` to fetch and
extract the URL again. `script URL --json` reports the source directory, word
count, omissions, and outline headings found, restored, or missing.

The extractor restores H2 and H3 headings when their section text can be
matched to the extracted Markdown. Some pages omit headings from extraction;
the repair report makes that visible. Verbatim scripts normalize prose and
report omitted content such as code, large tables, and math.

Only public HTML pages (`text/html` and `application/xhtml+xml`) and PDF
documents are supported. Login pages and JavaScript-only pages are not
extracted. For a page that requires browser access, save it and use
`--html FILE`:

```bash
sase-listen script https://example.com/article --html saved-page.html
```

If a site blocks automated requests, the command explains how to retry with a
saved HTML file. Pages shorter than 150 extracted words are rejected to catch
login screens, abstracts, and error pages.

## Brief and full editions

URL renders default to an AI-written `brief` adaptation. Choose `full` to cover
the article's major sections in order, or `verbatim` for the deterministic
article-text reading. A full edition is an adaptation within a 2,400-word
budget, not a word-for-word transcript. Brief targets about 600 words.

Review a generated script before rendering it:

```bash
sase-listen script https://example.com/article -e full -o article_narration.md
sase-listen render article_narration.md
```

The writer uses Gemini and the existing Gemini engine credentials. Generated
scripts and writer metadata are cached beside the stored source; a matching
source, model, edition, and prompt version reuses the script. Pass `--refresh`
to fetch and write again. Script writing uses Gemini API tokens and incurs model
usage charges. `render --dry-run` writes the script and reports the later audio
synthesis estimate.

## PDF documents

PDF URLs work everywhere an article URL does (`render`, `script`, all three
editions `brief` / `full` / `verbatim`, caching, `--refresh`, auto-publish).
A URL is treated as a PDF when its content type is `application/pdf` (or
`application/x-pdf`), or when an octet-stream or missing content type carries
`%PDF` bytes:

```bash
sase-listen render https://arxiv.org/abs/2608.25174 -e full
```

Local `.pdf` files are valid sources too, cached by content hash so re-renders
of the same file reuse the extraction:

```bash
sase-listen render paper.pdf -e full
sase-listen script paper.pdf -e verbatim --json
```

`render URL --html saved.pdf` keeps the URL identity for a PDF that had to be
downloaded by hand.

Extraction runs locally with pdfminer.six. Headings come from the PDF bookmark
outline when present, else from font sizes (large text, or short bold lines
with section numbering). Dropped along the way: running headers and footers,
page numbers, footnotes and other small text, table cells, numeric citation
brackets like `[12]`, the page-1 author/affiliation block, boilerplate
(`CCS Concepts`, `Keywords`, copyright notices), and the references section.
Kept metadata: the document title, a speakable author credit ("A and
colleagues" for four or more authors), the arXiv date and `arXiv` site when the
arXiv stamp is present, and the page count.

Limits: 64 MiB, 400 pages, no OCR — scanned PDFs without a text layer are
rejected with "The extracted PDF text is too short". Editions and caching match
articles, and `script … --json` reports the stored `source_dir`, word count,
and outline diagnostics (`found`, `restored`, `missing`, plus the outline
`source`: `bookmarks`, `fonts`, or `none`).

### arXiv papers

arXiv paper URLs are recognized on `arxiv.org`, `www.arxiv.org`, or
`export.arxiv.org`: `/abs/<id>`, `/html/<id>`, and `/pdf/<id>` (with or
without a `.pdf` suffix). Old- and new-style IDs are accepted, any query or
fragment is ignored, and the version suffix (for example `v2`) is kept. Each
is fetched as `https://arxiv.org/pdf/<id>`, so all forms for one paper share
one cached source, writer script, and episode. `--html FILE` keeps the URL as
given. A failed PDF fetch is reported and does not fall back to the abstract
page.
