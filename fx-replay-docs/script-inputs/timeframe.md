# timeframe

Creates a **timeframe (resolution) selector** so users can choose which timeframe your logic should run on (for example 5m, 1h, 1D). This is essential for multi-timeframe indicators, higher-timeframe filters, or confirmation logic — without forcing users to edit code.

### Syntax (one line)

`input.timeframe(title, value?, id?, group?, tooltip?, inline?, includeChartOption?)`

### Parameters

`title` *(string)* · Label shown in the inputs panel (e.g., `"Timeframe"`).\
`value` *(string)* · Default timeframe value (must be one of the allowed aliases).\
`id` *(string)* · Optional unique identifier (defaults to a simplified version of `title`).\
`group` *(string)* · Optional group name to organize inputs in the UI.\
`tooltip` *(string)* · Optional helper text shown on hover.\
`inline` *(string)* · Optional inline key to place inputs on the same row.\
`includeChartOption` *(boolean)* · If `true`, adds `"Chart"` as an option (uses the current chart timeframe).

### Allowed Timeframe Values

(case-insensitive)

* `"1m"`, `"3m"`, `"5m"`, `"15m"`, `"30m"`, `"45m"`
* `"1h"`, `"2h"`, `"3h"`, `"4h"`
* `"1D"`, `"1W"`
* `"Chart"` *(only if `includeChartOption` is true)*

> ⚠️ Any value outside this list will throw an error.

### Return Value

*(string)* · The selected timeframe value, which you can use inside `onTick`.

### Example

**What this does:**\
Adds a timeframe selector so the user can choose whether calculations run on the current chart timeframe or a higher one (like 1H or 1D).&#x20;

<pre class="language-javascript"><code class="lang-javascript"><strong>//@version=1
</strong>
init = () => {
  // Show the indicator in the main panel
  indicator({ onMainPanel: true, format: 'inherit' });

  // Create a timeframe input
  input.timeframe(
    "Calculation Timeframe",
    "Chart",                 // default value
    "calc_tf",               // id
    "Inputs",                // group
    "Select the timeframe used for calculations",
    undefined,
    true                     // include "Chart" option
  );
};

onTick = (length, _moment, _, ta, inputs) => {

};
</code></pre>

### Result

<figure><img src="/files/QWOvr1I3QYXDfkkPSizQ" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Use `input.timeframe` when you want to add **higher-timeframe confirmation** (for example: trend on 1H, entries on 5m).
* `"Chart"` is ideal as a default option so the script behaves normally unless the user explicitly changes it.
* Pair it with `input.src` so users can fully control **what price** and **what timeframe** drive your logic.
* Keep timeframe inputs grouped (using `group`) to avoid clutter in indicators with many settings.
  {% endhint %}

{% hint style="danger" %}

#### Warning

* Passing an unsupported value (like `"10m"` or `"2D"`) will throw an error — always stick to the allowed aliases.
* Changing timeframe inputs can significantly alter indicator behavior; consider mentioning this in your script description or tooltip.
* If you plan to fetch data from another timeframe, ensure your logic handles **delayed updates** correctly (higher TFs update less frequently).
  {% endhint %}

{% hint style="success" %}

#### Good Practice

* Default to `"Chart"` so beginners are not confused by unexpected results.
* Name plots or labels using the selected timeframe (e.g., `"EMA (1H)"`) to make chart output self-explanatory.
* If your script mixes multiple timeframes, clearly separate them with distinct inputs (e.g., `"Trend TF"`, `"Signal TF"`).
* Always document what the timeframe affects — calculations, filters, or confirmations — so users know what they are changing.
  {% endhint %}
