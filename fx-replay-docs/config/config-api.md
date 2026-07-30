# Config

The **Config API** is used to set up your indicator’s **panel placement** (main chart or separate panel) and its **number format** (price, percent, volume). It ensures your indicator displays in the right place with the correct precision for values.

## Config API <a href="#config-api" id="config-api"></a>

> **File:** `config‑indicator.api.ts`

### Syntax

`indicator(options)`

### Parameters

`options` is an object with the following fields:

* **onMainPanel** `boolean` —
  * `true` → Overlay the main price chart.
  * `false` → Show in a separate panel below.
* **format** `'inherit' | 'price' | 'percent' | 'volume'` —
  * `"inherit"` → Use the chart’s format automatically.
  * `"price"` → Format values as price (requires precision).
  * `"percent"` → Format values as percentage (requires precision).
  * `"volume"` → Format values as volume (requires precision).
* **precision** `number` (required only if `format` ≠ `"inherit"`) —\
  Number of decimal places used in display (e.g., `2` → `1.23`).

### Return Value

`{ is_price_study: boolean, format: object }` — Metadata object used by the platform when rendering the indicator.

### Example

```ts
// Example 1: Overlay indicator on main chart with chart’s format
meta = indicator({
  onMainPanel: true,
  format: 'inherit'
})

// Example 2: Indicator in a separate panel with 4 decimal places
meta = indicator({
  onMainPanel: false,
  format: 'price',
  precision: 4
})
```

{% hint style="info" %}

### Tips

* Use `"inherit"` when your indicator should follow the chart’s symbol format (stocks, forex, crypto, etc.).
* Use `"price"` with explicit precision when dealing with calculations that require fixed decimals (like indicators in crypto with 8 decimals).
  {% endhint %}

{% hint style="danger" %}

### Warning

If you choose `"price"`, `"percent"`, or `"volume"` without `precision`, the function will **throw an error**.
{% endhint %}

{% hint style="success" %}

### Good Practice

* For overlays (like moving averages), set `onMainPanel: true`. For oscillators (like RSI), set `onMainPanel: false`.
* Be explicit with precision when needed—especially in assets with very small price steps (crypto, commodities).
  {% endhint %}
