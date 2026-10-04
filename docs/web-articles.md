# Web articles

`script` and `render` accept public article URLs. Pages are fetched on this
machine with a browser-like HTTP client, then extracted locally; no hosted
reader receives the URL or page text.

```bash
sase-listen script https://example.com/article --json
sase-listen render https://example.com/article --edition verbatim --dry-run
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

Only public HTML pages (`text/html` and `application/xhtml+xml`) are supported.
PDFs, login pages, and JavaScript-only pages are not extracted. For a page that
requires browser access, save its HTML and use `--html FILE`:

```bash
sase-listen script https://example.com/article --html saved-page.html
```

If a site blocks automated requests, the command explains how to retry with a
saved HTML file. Pages shorter than 150 extracted words are rejected to catch
login screens, abstracts, and error pages.
