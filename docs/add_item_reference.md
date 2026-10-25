# `two_spoons.add_item` reference

Added in 0.4.0. Adds free text to a Two Spoons list the way Quick Add does.

## Action

`two_spoons.add_item`. The response is optional (`SupportsResponse.OPTIONAL`):
call it with `response_variable` to get the result.

After any request is sent, the integration refreshes its data, whether the
request worked or failed.

## Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `item` | string | yes | Free text as spoken, e.g. `"2 litres of milk"`. Spaces at either end are trimmed. Blank text, or more than 200 characters, is refused before anything is sent. |
| `store` | string | no | A store code. Trimmed and upper-cased (`" gg "` → `GG`), then checked against the stores from the last `/lists` poll. Left out: only `{text}` is sent and Two Spoons picks the store from the text. |
| `config_entry_id` | string | no | Only needed when more than one Two Spoons entry is set up. With exactly one, that one is used. |

## Response

| Key | Type | Meaning |
|---|---|---|
| `summary` | string | The item as the list shows it, e.g. `"Milk — 2 L"` (name, then ` — ` and the quantity). |
| `store_code` | string or null | The store it went to; with no `store` given, the one Two Spoons chose. Null only if that store has no code, which shouldn't happen for a store Home Assistant can see. |
| `store_name` | string | The store's name, e.g. `"Woolies"`. |
| `merged` | bool | True if the item was merged into one already on the list. |
| `merged_into_summary` | string or null | Null unless `merged` is true. When true, it's the existing item's summary after the merge, e.g. `"Oat milk — 4 L"`. That is the same text as `summary`, because the API returns the item it merged into. |

## Errors

Home Assistant removes the final full stop from exception messages, so the
messages below are exactly what Home Assistant gives. Errors from bad input
are `ServiceValidationError`; the rest are `HomeAssistantError`. All of them
have `translation_domain` `two_spoons`.

| Case | translation_key | Message |
|---|---|---|
| API 400 `invalid_body` / `invalid_json`, or text that's blank or over 200 characters (checked before sending) | `invalid_item` | Two Spoons couldn't add that item. Try saying it again, more briefly |
| Store code not in the last `/lists` poll (nothing sent) | `unknown_store` | Two Spoons has no store with the code {store}. The codes are {codes} |
| API 400 `unknown_store`, e.g. the store was removed since the last poll, or the last `/lists` poll failed | `unknown_store_code` | Two Spoons has no store with the code {store} |
| 401 (also starts re-authentication) | `add_item_invalid_auth` | Two Spoons rejected Home Assistant's token, so nothing was added. Re-enter the token in Home Assistant |
| 403 from the Vercel firewall (`x-vercel-mitigated`) | `add_item_blocked_by_firewall` | Two Spoons is blocking Home Assistant for now, so nothing was added. Try again later |
| 409 `no_shopping_week` | `add_item_no_shopping_week` | Two Spoons has no shopping week right now, so nothing was added |
| Timeout (30 s) | `add_item_timeout` | Two Spoons took too long to answer. The item may still have been added, so check the list before trying again |
| 5xx, network error, or a response it can't read | `add_item_cannot_connect` | Something went wrong talking to Two Spoons. Check the list to see whether the item was added |
| Any other API error code | `add_item_rejected` | Two Spoons couldn't add that item ({code}) |
| More than one entry and no `config_entry_id` | `choose_entry` | More than one Two Spoons is set up. Choose one with config_entry_id |
| No entry loaded, or a `config_entry_id` that isn't a loaded Two Spoons entry | `not_loaded` | Two Spoons isn't set up or isn't loaded |

`{store}` is the code as sent (upper-cased). `{codes}` lists the codes from
the last `/lists` poll, comma-separated in app order, e.g. `GG, WW, BK`.
`{code}` is the API's error code.

After a timeout or a server error, the item may already have been added, so
those messages say so rather than "nothing was added". A voice intent
shouldn't retry automatically in those cases.
