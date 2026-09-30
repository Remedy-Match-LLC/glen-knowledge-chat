# Solution category pages

Design approved by Glen in the clinical tab on 2026-09-30. Spec written by clinical.

## Why

remedymatch.com/resources lists 138 healing tools and resources in one flat list. The new site
has nothing like it. The remedy table also names solution categories, for example "EMF
reduction resources" and "Fasting resources". They read as products, yet no page exists to link
them to.

Glen's rule for how a visitor moves: **protocol page, then category page, then product pages.**
The protocol page teaches, and it lives at `/learn/<topic>`. The category page gives the
principle and the choices, then lists the products. The product page sells. When a protocol has
only one related product, it links straight to that product page.

This spec covers the category pages and their hub only. Moving the clinicalpraxis.com protocol
content into `/learn/`, and wiring the pattern pages, chatbot and reports to it, are separate
pieces with their own specs.

## Pages

- **`/solutions/`**, the hub. One card per category: title, a one-line principle, and a link.
- **`/solutions/<slug>`**, one per category. It holds a short principle of one or two
  paragraphs, "choose by intended use" notes, then product cards. Each card shows the product's
  name, image and price from the catalogue, and links to `/begin/product/<slug>`.
- **Links out.** Each category page can name the `/learn/` pages it teaches from, and link to
  them as "Learn more".
- **Unknown slug.** `/solutions/<unknown>` returns 404, not a placeholder page.

Both pages are rendered by the Flask app in the site's existing style, as the `/learn/` pages
are.

## Data: `data/solution_categories.json`

One file is the single source. Product details are never copied into it. They are read from
`data/products.json` when the page renders.

```json
{
  "categories": [
    {
      "slug": "emf-protection",
      "title": "EMF Protection",
      "principle": "One or two paragraphs in Glen's voice.",
      "choose_by_use": ["Short note on which item suits which use."],
      "products": ["product-slug", "..."],
      "learn": ["learn-topic-slug"],
      "table_names": ["EMF reduction resources"]
    }
  ]
}
```

`table_names` holds the remedy table's names for the category, so a table row can be traced to
its category page.

## The ten categories

| Slug | Title | Drawn from the old store | Remedy table names |
|---|---|---|---|
| emf-protection | EMF Protection | EMF, Mithreal silver-lined clothing, neutralizers, pouches | EMF reduction resources |
| water-hydrogen | Water & Hydrogen | Water, Hydrogen: plate ionizers, filters, H2 | |
| air | Air | Air purifiers and ionizers | |
| light-photobiomodulation | Light & Photobiomodulation | Light, Photobiomodulation: lasers, helmets | Photobiomodulation; Infrared & Red Light Photobiomodulation; Infrared |
| pemf | PEMF | PEMF: Kloud mats, miHealth | |
| microcurrent | Microcurrent | Microcurrent: DENAS, Microgen | |
| frequency-sound | Frequency & Sound | Frequency, Sound, 172 Hz tools: tuning forks, bowl, laser, helmet | 172 Hz |
| fasting | Fasting | none in the old store, so new | Fasting resources |
| stones-wearables | Stones & Wearables | Stone & Gems, Wearables | |
| books | Books | Print, ebooks, audio | |

Services (consulting, coaching, courses, EVOX Session) are not tools. They are out of scope.

Membership starts from the old store's hidden category pages, listed by the 2026-09-30 crawl.
Each item is kept only if it exists and is active in `data/products.json`. Items with no new-site
product are listed for Glen, and are never added as placeholders.

## Rules the data must obey

- Every product slug exists in the catalogue and is active.
- Every category has at least one product. Fasting may be the exception, since it may start
  with teaching only. If so, it must name at least one `/learn/` page.
- No do-not-recommend product appears. The **Living Water prill-bead bottle and its filter
  refill** are excluded. The old store lists the refill under Environment. The Living Water
  **plate ionizers** are included, since they are the primary recommendation.
- Every remedy table name that is a category appears in exactly one category's `table_names`.

## Copy

The principle and choose-by-use text are drafted in Glen's voice. The drafts start from his
clinicalpraxis.com pages where they exist, such as `/pemf` and `/photobiomodulation` ("Principle
before product. Choose by intended use."). They describe what a tool supports, never what it
treats. They get a copy round, three review rounds, and Glen's approval before publishing.

## Checks

- A data test enforces the rules above against the real catalogue.
- A route test covers the hub, one category, an unknown slug (404), and an inactive product,
  which must be dropped rather than render broken.
- A render check drives both pages and confirms the product cards and links. It checks the
  page, not only the payload.

## Who does what

- **Clinical:** this spec and the category data, meaning the membership lists and table names.
- **Glen:** approves the copy and the membership lists.
- **Platform:** builds the routes and templates, and merges on Glen's word.
- **Marketing:** is told, because this is part of its Website Master Plan phase 2
  (ClinicalPraxis migration and interlinking).

## Not in this piece

- Moving the clinicalpraxis protocol content into `/learn/`.
- Linking the pattern pages, chatbot and reports to protocol pages.
- A console editor for categories. The JSON file is edited by pull request until editing is
  frequent.
