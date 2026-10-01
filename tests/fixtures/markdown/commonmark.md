# CommonMark corners

Edge cases from everyday CommonMark: emphasis, references, rules, and quotes.

## Emphasis and code

*Single stars* and **double stars** become plain words. So do __underlines__
and ~~strikes~~. A ``code span with `backtick` inside`` reads as words.

A reference link [points here][target] without speaking the URL.

[target]: https://example.com/target

Autolinks like <https://example.com/auto> vanish from speech.

AT&amp;T keeps its entity. 3.14 stays exact.

---

> Nested quotes
>
> > unwrap twice.

3. Ordered lists keep their order
4. Starting numbers are not spoken

The letters a, b, and c stay lowercase. Done.
