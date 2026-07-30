# shapes

This lets you place **markers or icons** directly on your chart, like arrows, circles, or text labels. Think of it as a way to visually highlight signals: for example, put a green arrow below the bar when you want to show a “buy signal,” or a red arrow above the bar for a “sell signal”.

### Syntax

`plot.shapes(title, value, text, color, textColor, plottype, location, size, offset?, transparency?, id?)`

### Parameters

* **title** `string` — Name of the shape series (shown in settings).
* **value** `number` — The bar or point where the shape should appear.
* **text** `string` — Text label to show with the shape (optional, can be empty).
* **color** `string` — Color of the shape (like `"green"` or `"rgba(0,255,0,0.8)"`).
* **textColor** `string` — Color of the text label.
* **plottype** `PlotShapeId (string)` — What kind of shape to draw (arrow up, arrow down, circle, square, etc.).
  * `shape_arrow_down`
  * `shape_arrow_up`
  * `shape_circle`
  * `shape_cross`
  * `shape_xcross`
  * `shape_diamond`
  * `shape_flag`
  * `shape_square`
  * `shape_label_down`
  * `shape_label_up`
  * `shape_triangle_down`
  * `shape_triangle_up`
* **location** `MarkLocation (string)` — Where to place the shape (above bar, below bar, on price).
  * `AboveBar`
  * `BelowBar`
  * `Top`
  * `Bottom`
  * `Right`
  * `Left`
  * `Absolute`
  * `AbsoluteUp`
  * `AbsoluteDown`
* **size** `PlotSymbolSize (string)` Size of the shape.
  * `auto`
  * `tiny`
  * `small`
  * `normal`
  * `large`
  * `huge`
* **offset** `number`  (Optional) Bar offset.\
  Positive values move the shape to the right, negative values to the left.
* **transparency** `number` (optional) — How transparent the shape should be (0 = solid, higher = more transparent).
* **id** `string` (optional) — Custom identifier if you want control.

### Return Value

`{ value, id }` — The shape data and its identifier.

### Example

```ts
//@version=1
// --------------------
// Init (config & inputs)
// --------------------
init = () => {
    // Indicator on the main chart panel
    indicator({ onMainPanel: true, format: 'inherit' });

    // Toggle to enable/disable signals
    input.bool('Show signals', true, 'showSignals', 'Signals', 'Show markers');
};

// --------------------
// onTick (logic)
// --------------------
onTick = (length, _moment, _unused, ta, inputs) => {
    const showSignals = inputs.showSignals;

    if (!showSignals) return;

    // Draw only every 40 bars (change 40 to any value you want)
    if (index % 40 === 0) {
        const o = openC(0);
        const c = closeC(0);

        if ([o, c].some((v) => v === undefined || isNaN(v))) return;

        const isBull = c >= o;

        if (isBull) {
            // BUY: green arrow below the bar
            plot.shapes(
                'Signals',
                c,
                'BUY',
                'rgba(0, 200, 0, 0.9)',
                '#FFFFFF',
                'shape_arrow_up',
                'BelowBar',
                'normal',
                0,
                0,
                'buy_signal',
            );
        } else {
            // SELL: red arrow above the bar
            plot.shapes(
                'Signals',
                c,
                'SELL',
                'rgba(220, 0, 0, 0.9)',
                '#FFFFFF',
                'shape_arrow_down',
                'AboveBar',
                'normal',
                0,
                0,
                'sell_signal',
            );
        }
    }
};

```

### Result

<figure><img src="/files/o009IpycoRCLJNOxaxaZ" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Use arrows for buy/sell, circles or labels for special events.
* You can mix shapes and text: for example, a red arrow with “Sell” on it.
  {% endhint %}

{% hint style="danger" %}

### Warning

* If you don’t choose the right `location`, your shape might overlap price bars or get lost in the chart.
* Avoid using too many shapes at once, or your chart will look messy.
  {% endhint %}

{% hint style="success" %}

### Good Practice

* Keep your signals clear: green for buy, red for sell, yellow for alerts.
* Use shapes to highlight the *important moments* only, not every single bar.
  {% endhint %}
