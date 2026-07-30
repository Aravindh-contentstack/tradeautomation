# line

This adds a **line on your chart** that moves with your data. It’s the most common way to show things like moving averages, signals, or any custom calculation. For example, you can plot a 14-period moving average or draw your custom indicator line on top of price.

### Syntax

`plot.line(title, value, color, plottype?, histogramBase?, offset?, id?)`

### Parameters

* **title** `string` — Name of the line (shown in settings).
* **value** `number` — The number you want to plot (for example: `close`, `sma`, `rsi`).
* **color** `string` — Line color, like `"``color.red"`.
* **plottype** `number` (optional) — How the line is drawn (usually just leave it as default).
  * `plottype`- number, one of the following:
    * `0`- line
    * `1`- histogram
    * `3`- cross
    * `4`- area
    * `5`- columns
    * `6`- circles
    * `7`- line with breaks
    * `8`- area with breaks
    * `9`- step line
* **histogramBase** `number` (optional)—Baseline used when `plottype` is a histogram. Values are drawn relative to this level.
* **offset** `number`(optional)—Shifts the plot forward or backward in time (positive or negative values).
* **id** `string` (optional) — Custom identifier (helpful if you want to link it later, for example with `filledArea`).

### Return Value

`{ value: number; id: string }` — Echoes the `value` and the final `id` assigned to the plot.

### Example

```ts
//@version=1
init = () => {
  // Show the indicator in the main panel
  indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = () => {
  // 1) Calculate some simple values to plot
  const cierre = closeC(0);              // closing price of the current candle
  const hl2 = (high(0) + low(0)) / 2;    // average (high+low)/2 of the current candle

  // 2) Plot a line of the CLOSING PRICE
  //    plot.line(title, value, color, plottype?, id?)
  //    - title: name the user will see in the legend
  //    - value: number to plot for THIS candle
  //    - color: line color
  //    - plottype: (optional) line style (0 by default)
  //    - histogramBase : (optional) Baseline used when plottype is a histogram (0 by default)
  //    - offset : (optional) Shifts the plot (0 by default)
  //    - id: (optional) unique identifier; if not passed, it is derived from the title
  plot.line("Close", cierre, color.blue, 0);

  // 3) Plot another line (HL2) with a custom id to avoid collisions
  plot.line("Current HL2", hl2, color.gray, 0, 0, 1, "hl2_actual");

  // 4) Plot the High of the candle
  plot.line("High", high(0), color.yellow, 0);
};

```

### Result

<figure><img src="/files/xczU7yOvqX9xT7yMJuuH" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Give lines clear names like *“Fast MA”* or *“RSI Signal”* so users know what they see.
* If you only need one line, don’t worry about `id`. If you want to connect it with another plot (like filling the area between two lines), give it a custom `id`.
  {% endhint %}

{% hint style="danger" %}

### Warning

If you name two lines the same, they’ll get the same `id` and might overwrite each other. Use unique names \
or custom `id`s.
{% endhint %}

{% hint style="success" %}

### Good Practice

* Use high-contrast colors (blue, red, green) so your lines stand out clearly against both light and \
  dark charts.
* Keep overlays (like SMA, EMA) on the main chart, and put oscillators (like RSI) in their own panel.
  {% endhint %}
