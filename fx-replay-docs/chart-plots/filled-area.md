# filledArea

This lets you **fill the space between two lines** with a color. It’s perfect for showing zones like **Bollinger Bands**, **channels**, or shaded areas between support and resistance. Instead of just seeing two lines, the area between them becomes highlighted, making it much easier to read the chart.

Imagine:

* Shading the area between an **upper and lower band**.
* Coloring the space between a **fast MA and slow MA**.
* Highlighting a **support/resistance zone**.

### Syntax

`plot.filledArea(id, objATitle, objBTitle, title, color, transparency?, visible?, type?)`

### Parameters

* **id** `string` — A unique name for this filled area.
* **objATitle** `string` — The title of the **first line** (must already exist).
* **objBTitle** `string` — The title of the **second line** (must already exist).
* **title** `string` — Label for this filled area.
* **color** `string` — The color used to fill the area (like `"color.red"`).
* **transparency** `number` (optional, default = `90`) — How transparent the fill is.
* **visible** `boolean` (optional, default = `true`) — Show or hide the filled area.
* **type** `FilledAreaType` (optional) — Defines the type of area (usually not needed, default works).

  • **TypeHlines** = `"hline_hline"` Filled area type for bands.\
  • **TypePlots** = `"plot_plot"` Filled area type for plots.

### Return Value

Nothing (it just creates the fill on the chart).

### Example

```ts
//@version=1

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
    indicator({ onMainPanel: true, format: 'inherit' });
}

// This is the main function. It is called every time a new candle is received.
// Here you can calculate the indicator values and draw the indicator on the chart.
onTick = (length, _moment, _, ta, inputs) => {
    plot.line("HIGH", high(0), color.red, 0);      // upper line (candle high)
    plot.line("LOW", low(0), color.blue, 0);       // lower line (candle low)
    
    plot.filledArea("hi_lo_fill", "HIGH", "LOW", "HL Range", color.red, 85, true, "plot_plot");
};

```

### Result

<figure><img src="/files/KAhNH6fsmANNtLzNaSEy" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Filled areas make it much easier for users to “see the zone” instead of just comparing two lines.
* Use transparent colors so the price bars are still visible behind the shading.
  {% endhint %}

{% hint style="danger" %}

### Warning

* Both lines (`objATitle` and `objBTitle`) **must exist first**, or the fill won’t appear. Always create the \
  lines before the fill.
* Don’t use too many filled areas — the chart can get heavy and hard to read.
* Build the method in one line, if you separate in different lines it won't work.
  {% endhint %}

{% hint style="success" %}

### Good Practice

* Use filled areas for “zones” (bands, ranges) and lines for “signals.”
* Stick to light shading (like pale blue, light green, or light red) so it doesn’t overpower the chart.
  {% endhint %}
