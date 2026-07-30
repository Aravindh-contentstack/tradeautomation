# barColorer

This tool lets you **change the actual color of the candlesticks or bars** on your chart. Instead of just adding lines or shapes, you can make the candles themselves turn **green, red, yellow, or any color** based on your rules.

For example:

* Make candles **green** when the trend is bullish.
* Make candles **red** when the trend is bearish.
* Highlight special conditions like **yellow candles** during high volatility.

It’s a powerful way to make the chart itself tell a story at a glance.

### Syntax

`plot.barColorer(title, value, colors, id?)`

### Parameters

* **title** `string` — Name of this bar colorer (shown in settings).
* **value** `number` — A value you pass in (often your condition’s result).
* **colors** `[{ color: string, name: string }]` — A list of colors with labels. Example: `{ color: "green", name: "Up" }`.
* **id** `string` (optional) — Custom identifier, if you want control over naming.

### Return Value

`{ value, id }` — The bar colorer data and its identifier.

### Example

```ts
//@version=1
init = () => {
  // Show the indicator on the main chart panel with inherited formatting
indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = () => {
  // 1) Define a small color palette.
  //    IMPORTANT: barColorer maps palette entries starting at index 1 internally.
  //    (Your method shifts each color to idx+1.)

  // 2) Decide which palette index to use for the current bar.
  //    If close > open → use 1 (Bull), else use 2 (Bear).
  const idx = closeC(0) > openC(0) ? 1 : 2;

  // 3) Color the current bar using the chosen palette index.
  //    barColorer(title, value(index), colors[], id?)
  plot.barColorer("Bar Colors", idx, [
    { name: "Bull", color:color.yellow }, // will map to palette index 1
    { name: "Bear", color: color.blue }  // will map to palette index 2
  ]);
};

```

### Result

<figure><img src="/files/rqI2DLZ1tlbaqSyRoQr2" alt=""><figcaption></figcaption></figure>

{% hint style="warning" %}
&#x20;⚠️ ATTENTION ⚠&#xFE0F;**:** When you change the bar color with `barColorer`, you may notice that the displayed color looks different from the one you selected. This happens because the base candle color underneath is still rendered. If you want a perfect match, adjust the background or transparency parameters to get the desired result.
{% endhint %}

<figure><img src="/files/mrxSRx58uDZPWmK5Ptdd" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Great for strategies — you can make the entire chart flash your signals by changing candle colors.
* Use soft or transparent versions of colors if you don’t want to completely override the chart’s \
  original look.
  {% endhint %}

{% hint style="danger" %}

### Warning

* If you assign too many colors or confusing rules, your chart may look messy. Keep it simple.
* Double-check your conditions — otherwise you might end up painting *every* bar the same color.
  {% endhint %}

{% hint style="success" %}

### Good Practice

* Use **green/red** for main trend signals, and other colors (yellow, blue, purple) for special conditions.
* Keep names meaningful (like *“Trend Bars”* or *“Volatility Highlight”*) so users understand what the \
  coloring means.
  {% endhint %}
