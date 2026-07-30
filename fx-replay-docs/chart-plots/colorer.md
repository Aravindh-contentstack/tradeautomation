# colorer

This lets you **color a line differently depending on conditions**. Imagine you have a moving average: you can make it **green when the trend is up** and **red when the trend is down**. Instead of drawing multiple lines, you just tell the system to change colors automatically.

It’s perfect for showing when your indicator switches between bullish/bearish, or highlighting specific states in your strategy.

### Syntax

`plot.colorer(title, value, plotTargetTitle, colors, id?)`

### Parameters

* **title** `string` — Name of this colorer (shown in settings).
* **value** `number` — Data you want to “attach” to (often the same as the target line).
* **plotTargetTitle** `string` — The **title of the line** you want to color.
* **colors** `[{ color: string, name: string }]` — A list of colors and labels (like `{ color: "green", name: "Up" }`).
* **id** `string` (optional) — Custom identifier, if you want control over naming.

### Return Value

Gives back `{ value, id }`. Usually you’ll just care that your target line is now color-aware.

### Example

```ts
//@version=1
init = () => {
    // Configure the indicator in the main chart panel
    indicator({ onMainPanel: false, format: 'inherit' });
};

// Example: Color a moving average depending on price relation
const closeSeries = [];

onTick = (length, _moment, _, ta) => {
    const close = closeC(0);
    closeSeries.push(close);

    if (closeSeries.length < 20) return;

    // Calculate a 20-period SMA
    const smaArray = ta.sma(closeSeries, 20);
    const sma = smaArray.at(-1);

    // Draw the SMA line (this is the target we will color later)
    plot.line("SMA20", sma, "#AAAAAA");

    // Choose the palette index
    const paletteIndex = close > sma ? 0 : 1;

    // Apply the colorer to the target line "SMA20"
    plot.colorer("SMA20 Colorer", paletteIndex, "SMA20", [{ name: "Price Above", color: "green" }, { name: "Price Below", color: "red" }]);
};

```

* `plot.line("SMA20", sma, "#AAAAAA")` draws the base SMA line.
* `plot.colorer("SMA20 Colorer", paletteIndex, "SMA20", palette)` changes its color **per bar** using the palette.
* If the close is above the SMA → index `0` → green. If below → index `1` → red.

This way the same line is automatically recolored bar by bar, just like a **dynamic trend indicator**

### Result

<figure><img src="/files/u5kRTM5zmsxRgGbAc4BW" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Always set up the target line first, then the colorer. The colorer attaches to it.
* Use meaningful color names in your options (`Up`, `Down`, `Neutral`) so they make sense in settings.
  {% endhint %}

{% hint style="danger" %}

### Warning

* If you type the target line’s title wrong (`plotTargetTitle`), the colorer won’t find it and will fail. Double-check spelling!
* Don’t assign too many colors — 2 or 3 conditions are best. More can confuse users.
  {% endhint %}

{% hint style="success" %}

### Good Practice

* Use green/red for trend signals, but softer colors for neutral states so the chart doesn’t look messy.
* Keep your `title` short and clear, like *“RSI Colors”* or *“MA Trend”*.
  {% endhint %}
