# Changelog

## [0.1.2](https://github.com/sase-org/sase-listen/compare/v0.1.1...v0.1.2) (2026-10-08)


### Features

* **install:** rename venv recipe to install-venv with private install alias ([1b82d27](https://github.com/sase-org/sase-listen/commit/1b82d27f3eba92b3c77b87d6a60b90884bd69577))
* **listen:** make multi-machine installs self-diagnosing ([3f2937d](https://github.com/sase-org/sase-listen/commit/3f2937d2e3423a6905d4ac777b1e46b464e4d111))
* **listen:** ship sase listen as a first-class command plugin ([8c57128](https://github.com/sase-org/sase-listen/commit/8c5712886833b527bdb39197513c4701af225e74))


### Performance Improvements

* **cli:** defer heavy imports into command handlers for fast startup ([bbaf58f](https://github.com/sase-org/sase-listen/commit/bbaf58f7cdedecc54af5549cca1672afd71ba6e3))

## [0.1.1](https://github.com/sase-org/sase-listen/compare/v0.1.0...v0.1.1) (2026-10-08)


### Features

* **feedhost:** name SSH agent state on publickey denials ([7d59de1](https://github.com/sase-org/sase-listen/commit/7d59de1adf0e503fe89550d78a252e9433e05b37))
* **feed:** publish episodes to one SSH feed host from any machine ([0e03944](https://github.com/sase-org/sase-listen/commit/0e0394432210d4d7c7729328c79a4120189b0253))
* **feed:** supersede same-title episodes on publish with AntennaPod refresh notice ([f8154ad](https://github.com/sase-org/sase-listen/commit/f8154ad0708141e6cb7c674c1130015f262a8a2a))
* **guide:** default narration guide to brief edition ([3037d60](https://github.com/sase-org/sase-listen/commit/3037d60800299eda8d7d43d1da6fcb4cb208779c))
* **render:** add generated-cover override for research audio title cards ([627776d](https://github.com/sase-org/sase-listen/commit/627776d2945228777fe9d166e667f31ec7f91424))
* **render:** live stage checklist, chunk board, and safe Ctrl-C ([98feaab](https://github.com/sase-org/sase-listen/commit/98feaab6f9c45fdb44651662b6f7e006f2d87e1b))
* **render:** split-and-retry Gemini TTS content_blocked chunks ([ff7a90f](https://github.com/sase-org/sase-listen/commit/ff7a90f25e2491776413a8d93e6785a70773ebe5))
* **sase-listen:** render PDF sources (URLs and local files) ([355e649](https://github.com/sase-org/sase-listen/commit/355e64991659068263a5e5bb11ad5ec50a0ee13b))
* **web:** fetch and render article URLs ([e085c63](https://github.com/sase-org/sase-listen/commit/e085c63bc509d4d2dab076842c461961dce37f52))
* **web:** fetch arXiv paper URLs as their PDF ([eb05e7c](https://github.com/sase-org/sase-listen/commit/eb05e7c19ae1dfa5acb16f34d7cdc3edc12183f6))
* **writer:** add brief and full article editions ([9f491ac](https://github.com/sase-org/sase-listen/commit/9f491ac5d51cf7fedc8700c3d3adef6f0d45e232))


### Bug Fixes

* **audio:** master the MP3 beside its target so a tmpfs /tmp cannot break the replace ([870e069](https://github.com/sase-org/sase-listen/commit/870e0691e12a1fcdf151ae8fd9fd45d1d4143050))
* **feed:** report remote via as the SSH destination ([69a52a4](https://github.com/sase-org/sase-listen/commit/69a52a449a421126197748025fd0e3111fc38233))
* **lint:** match spelled-out source numbers in W013 number fidelity ([f64bc6c](https://github.com/sase-org/sase-listen/commit/f64bc6c5c74f6267d371e5ab3cb3fb55000ca68d))
* **sase-listen:** classify interactions compat errors and name credential source ([9da5586](https://github.com/sase-org/sase-listen/commit/9da558632952be1f51d150f233a7804ee44130e4))


### Documentation

* **rollout:** apollo field notes, audition sample clip, and home-page audio embed ([d74633a](https://github.com/sase-org/sase-listen/commit/d74633aae46c7f89d4b13b522295716d9ca3f1ee))
* **sase-integration:** document swarm listen card and audio waits ([ec1196b](https://github.com/sase-org/sase-listen/commit/ec1196b8d2f517dd4430a90d70ad19148c4dd5c1))

## 0.1.0 (2026-10-01)


### Features

* **audio:** mastering, MP3 packaging, chapters, and cover art ([45a0632](https://github.com/sase-org/sase-listen/commit/45a06329c70c10db697a52c35ffcd5a39f1940af))
* **engines:** TTS adapters, narrators, secrets, retry, cache, and pricing ([ae83bd0](https://github.com/sase-org/sase-listen/commit/ae83bd0908b18ee75b2c8753e03e9c3b9e8100e9))
* **feed:** private podcast feed for AntennaPod ([511a5b9](https://github.com/sase-org/sase-listen/commit/511a5b9750052ccb739ac8dbf596ddb6c0456099))
* **pipeline:** implement render orchestration with gates, mastering and manifest ([0b78d36](https://github.com/sase-org/sase-listen/commit/0b78d36a7c79ce8b2f2dbec8ba455866c0dc8985))
* scaffold sase-listen package, CLI registry, docs, CI, and release automation ([c35e6e2](https://github.com/sase-org/sase-listen/commit/c35e6e2ce35415f1081216edd21c469feb4040d0))
* **script:** narration script contract, normalizer, lexicon, lint, and guide ([9db7044](https://github.com/sase-org/sase-listen/commit/9db7044b378d8cae82cd447b962c8a5028aca30e))


### Documentation

* finish README and mkdocs site with provenance links ([d3cb303](https://github.com/sase-org/sase-listen/commit/d3cb3032be268910be173d939384cc19e5f95364))
