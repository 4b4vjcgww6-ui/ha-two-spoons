# Two Spoons for Home Assistant

A Home Assistant integration for the Two Spoons shopping list app. Each store
in Two Spoons becomes a Home Assistant to-do list, so you can see, add, tick
and remove items from Home Assistant, Assist and automations.

## What you get

- **One to-do list per store** for the current shopping week, on a single
  "Two Spoons" device. Items show as `Name — quantity`, with the item's
  category as the description.
- **Add, tick, untick, rename and remove** items. Adds go through Two Spoons'
  Quick Add, so quantities are understood and duplicates are merged.
- **A "Meals" calendar** with one all-day event per planned dinner for this
  week and next, plus weekend baking across Saturday and Sunday. The
  calendar's state is tonight's dinner (off when nothing is planned).
  Event descriptions have one line per detail that's set, always in this
  order: `kind:`, then for ready meals `method:`, `temp:` (°C), `cook:` (min)
  and `note:`. A ready meal's event shows its name once it's filled in.
  Read-only.
- **A "Week status" sensor**: `not_started` (no dinners planned in the
  shopping week, or no shopping week yet), `partial` (1–6) or `planned` (7),
  with the counts and tonight's dinner as attributes.
- **A "Woolies order" sensor** for the shopping week's click & collect order:
  `not_placed`, `exported`, `placed`, or `pickup_today` once placed on
  collection day (Perth time), with order/collection dates and item counts.
- **A "Tonight" sensor** for automations: the state is tonight's dinner, or
  `Not planned`. Attributes: `kind`, `cook_method`, `oven_temp_c`,
  `cook_mins`, `note`, `bought` (its Ready Meals item is ticked),
  `details_needed` and `date`. "Tonight" is the app's date in Perth, so the
  state moves on at the first poll after midnight there.
- **A "Ready meals needing details" sensor**: how many ready-meal nights the
  app says still need details (tonight, or a later night that's been bought,
  with the name or method missing, or an oven night missing its temperature
  or cook time). The `nights` attribute lists each one's `date` and `label`.
- **`two_spoons.refresh`**, an action that fetches everything straight away,
  for example when you arrive at a store.
- **`two_spoons.add_item`**, an action that adds free text the way Quick Add
  does (`item`, plus an optional `store` code; with no store, Two Spoons picks
  one). It can return the item as the list shows it, the store it went to and
  whether it merged into an existing item. Its errors are worded to be read
  aloud by a voice assistant.
- New stores appear automatically. A store that's removed or made inactive in
  Two Spoons shows as unavailable; its list is never deleted for you.
- The `WW` store is also known to Assist as "Woolworths".

Ready Meals days can be ticked from Home Assistant, but only renamed or removed
in Two Spoons. "Remove completed items" removes everything else and tells you
which Ready Meals days it left.

## Requirements

- Home Assistant 2026.10 or later.
- A Two Spoons site and a Home Assistant API token for your household.

## Installation (HACS)

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/4b4vjcgww6-ui/ha-two-spoons` with type **Integration**.
3. Install **Two Spoons** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Two Spoons**.

## Setup

| Field | What to enter |
|---|---|
| URL | Your Two Spoons site, e.g. `https://example.com`. The full API address (`…/api/ha/v1`) works too. |
| API token | Your household's Home Assistant token. |

The integration checks both before saving. If the token is later rejected,
Home Assistant asks for a new one. To change the URL or token at any time, use
**Reconfigure** on the integration.

**Options:** polling interval in minutes (default 5, minimum 2). Each poll
reads the lists, the meals and the week status. Changes made from Home
Assistant refresh straight away regardless.

## Example: refresh when you arrive at a store

```yaml
automation:
  - alias: "Refresh shopping lists at the shops"
    triggers:
      - trigger: zone
        entity_id: person.me
        zone: zone.shops
        event: enter
    actions:
      - action: two_spoons.refresh
```

## Example: add an item and say where it went

```yaml
script:
  add_to_shopping:
    fields:
      item:
        selector:
          text:
    sequence:
      - action: two_spoons.add_item
        data:
          item: "{{ item }}"
        response_variable: added
      - action: notify.notify
        data:
          message: >-
            {{ added.summary }} is on the {{ added.store_name }} list{{
            ' (merged)' if added.merged }}.
```

`config_entry_id` is only needed if more than one Two Spoons site is set up.

## Troubleshooting

- **"Blocked by the Vercel firewall"**: the Two Spoons site's firewall
  challenged a request. A repair notice appears, the integration waits 15
  minutes before trying again, and the notice clears itself once a request
  gets through.
- **Lists unavailable**: there's no shopping week right now, the store is
  inactive, or the site can't be reached. Download diagnostics from the
  integration page; the token is redacted and no item names are included.

## Development

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

## License

MIT
