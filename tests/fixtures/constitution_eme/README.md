# Constitutions of Europe, the Caucasus and the Middle East: parser fixtures

One JSON file per document of the 20 sources of the deep sweep (`docs/constitutions-europe-me-deep.md`). Each holds

* `lines`: real lines of the document as the publisher serves it, after the manifest's own HTML or PDF selection
  (`html_select`, `html_skip_*`, `drop_lines`, `encoding`, page selection): the front matter, the first articles, and,
  where the manifest names a `tail_regex`, the first twelve lines from there on. Nothing is rewritten.
* `hints`: the `structure` hints of the committed manifest that the text parser reads (the selection hints, which act
  before the lines are made, are left out).
* `title`, `citation_name`, `language`, `jurisdiction`, `media` and `source`: the manifest's own values.

`expectations.json` is the golden record of what the parser makes of each fixture (top-level units, kinds, first text,
last unit); `tests/test_constitution_eme.py` checks it and asserts the parts that matter by hand. Regenerate it only
after reading the changed parse: a change is either a parser fix or a regression.

The texts are excerpts of official publications kept for testing only (the sources are listed in the manifests under
`config/manifests/` with their licence reading in `config/sources/`); they are not a copy of the documents.
