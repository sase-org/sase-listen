# Changelog

## Unreleased

- Gemini TTS now classifies interactions-API compat errors (duck-typed
  `status_code`) with the same table as `errors.APIError`, including 400
  `API_KEY_INVALID` as credentials, 429/5xx as transient with retry, and
  connection errors as transient. Credential failures exit 3 with a
  secret-free hint naming the key source, and `doctor` reports the winning
  source. `resolve_api_key_with_source` added; `resolve_api_key` unchanged.

Releases are cut by release-please from Conventional Commit titles; each
release publishes to PyPI through trusted publishing. The full history lives
at <https://github.com/sase-org/sase-listen/releases> and the installable
versions at <https://pypi.org/project/sase-listen/>.

To propose the next release, merge the open `release-please` PR (see the
release phase of the epic). To check what you have installed:

```bash
sase-listen --version
```
