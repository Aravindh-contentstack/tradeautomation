# moment Library

> **Namespace exposed in indicators:** `_moment` (alias of `moment`)\
> Works exactly like [Moment.js 2.x](https://momentjs.com/), but only the methods listed below are guaranteed to be available.

***

### Global API

<table><thead><tr><th>Function / Constant</th><th width="309">Signature (simplified)</th><th>Returns / Purpose</th></tr></thead><tbody><tr><td><code>moment()</code></td><td><p><code>moment(inp?: MomentInput,</code> </p><p><code>strict?: boolean)</code></p></td><td><code>Moment</code> instance</td></tr><tr><td> </td><td><code>moment(inp, format, strict?)</code></td><td>Parse with format</td></tr><tr><td> </td><td><p><code>moment(inp, format, lang?,</code> </p><p><code>strict?)</code></p></td><td>Parse with locale</td></tr><tr><td><code>moment.utc</code></td><td><code>utc(inp?, format?, lang?, strict?)</code></td><td><code>Moment</code> in UTC</td></tr><tr><td><code>moment.unix</code></td><td><code>unix(timestamp: number)</code></td><td>UTC <code>Moment</code> from seconds</td></tr><tr><td><code>moment.parseZone</code></td><td><code>parseZone(inp?, format?, …)</code></td><td>Keep original offset</td></tr><tr><td><code>moment.duration</code></td><td><code>duration(inp?, unit?)</code></td><td><code>Duration</code> object</td></tr><tr><td><code>moment.locale</code></td><td><code>locale(lang?: string | string[])</code></td><td>Get / set active locale</td></tr><tr><td><code>moment.locales</code></td><td><code>locales()</code></td><td><code>string[]</code> of loaded locales</td></tr><tr><td><code>moment.localeData</code></td><td><code>localeData(key?)</code></td><td>Locale object</td></tr><tr><td><code>moment.isMoment</code></td><td><code>isMoment(obj)</code></td><td><code>boolean</code></td></tr><tr><td><code>moment.isDate</code></td><td><code>isDate(obj)</code></td><td><code>boolean</code></td></tr><tr><td><code>moment.isDuration</code></td><td><code>isDuration(obj)</code></td><td><code>boolean</code></td></tr><tr><td><code>moment.min</code></td><td><code>min(...moments)</code></td><td>Earliest <code>Moment</code></td></tr><tr><td><code>moment.max</code></td><td><code>max(...moments)</code></td><td>Latest <code>Moment</code></td></tr><tr><td><code>moment.now</code></td><td><code>now()</code></td><td>Unix ms (overridable)</td></tr><tr><td><code>moment.defineLocale</code></td><td><code>defineLocale(lang, spec)</code></td><td>Add locale</td></tr><tr><td><code>moment.updateLocale</code></td><td><code>updateLocale(lang, spec)</code></td><td>Patch locale</td></tr><tr><td><code>moment.normalizeUnits</code></td><td><code>normalizeUnits(unit)</code></td><td>Canonical unit string</td></tr><tr><td><code>moment.relativeTimeThreshold</code></td><td><code>relativeTimeThreshold(key, limit?)</code></td><td>Get / set threshold</td></tr><tr><td><code>moment.relativeTimeRounding</code></td><td><code>relativeTimeRounding(fn?)</code></td><td>Get / set rounding fn</td></tr><tr><td><code>moment.calendarFormat</code></td><td><code>calendarFormat(m, now)</code></td><td>Calendar token</td></tr><tr><td><strong>Constants</strong></td><td><code>version</code>, <code>ISO_8601</code>, <code>RFC_2822</code>, <code>defaultFormat</code>, <code>defaultFormatUtc</code>, <code>suppressDeprecationWarnings</code>, <code>deprecationHandler</code>, <code>HTML5_FMT</code></td><td>Metadata &#x26; helpers</td></tr><tr><td><code>moment.fn</code></td><td>—</td><td>Prototype holding <em>all</em> instance methods (use through any <code>Moment</code>)</td></tr></tbody></table>

***

### Common Instance Methods

| Method                  | Example                  | Purpose                  |
| ----------------------- | ------------------------ | ------------------------ |
| `format()`              | `m.format('YYYY‑MM‑DD')` | String formatting        |
| `add()` / `subtract()`  | `m.add(1, 'day')`        | Date math                |
| `diff()`                | `m.diff(other, 'hours')` | Time difference          |
| `startOf()` / `endOf()` | `m.startOf('day')`       | Snap to unit             |
| `isBefore` / `isAfter`  | `m.isBefore(other)`      | Comparison               |
| `unix()` / `valueOf()`  | `m.unix()`               | Timestamp (seconds / ms) |
| `clone()`               | `m.clone()`              | Immutable copy           |

### Quick example&#x20;

```ts
onTick = (length, _moment, _, ta, inputs) => {
  // current day of month (1‑31)
  const today = _moment().date();

  // parse a UTC timestamp
  const asUtc = _moment.utc(1632596400000).format('YYYY‑MM‑DD HH:mm');

  // 3‑hour duration
  const dur = _moment.duration(3, 'hours').asMinutes(); // 180
};
```
