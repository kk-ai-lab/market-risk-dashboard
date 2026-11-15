---
status: accepted
---

# Bilingual news display model

`news.json` keeps each article's source text until an external translation sidecar supplies the missing language. `lang` identifies the source language, and the UI uses it to choose a safe fallback.

## Stored fields

- `title` and `summary` hold the source text. English feeds therefore provide English text. Chinese feeds retain Chinese text until a sidecar supplies English.
- `title_zh` and `summary_zh` hold Chinese text when a translation is available. For a Chinese-source item, the sidecar copies the Chinese original and fills the English fields.
- `lang` records the source language. The UI displays Chinese-source text in Chinese and shows its localized unavailable label in English until a translation arrives.
- Translation fields may be partial. The UI does not display a value in the wrong language.

## Merge and freshness

`merge_translations` matches by item ID, with a normalized-title fallback for old sidecars whose IDs drifted. It fills Chinese fields for English-source articles. For Chinese-source articles, it fills the English fields and preserves the Chinese original.

The pipeline merges a sidecar only when its `updated_at` is at least as new as the latest article's `published_at`. An older sidecar cannot make a newer news batch appear translated. The pipeline does not generate translations or AI briefs.

## Decision history

The initial design required `title` and `summary` to always contain English. That invariant did not match Chinese feeds when no translation sidecar existed. Storing original feed text keeps the article usable in its source language and lets the UI identify missing translation without showing the wrong language.

The previous mirror-pair merge could overwrite English fields with Chinese text. This decision keeps the source text stable and puts translated text in the corresponding language fields.
