# Chart Plots

## Plots API <a href="#plots-api" id="plots-api"></a>

> **File:** `plots‑indicator.api.ts`\
> Draws series and shaded areas.

<table><thead><tr><th width="179">Method</th><th>Signature</th><th>Purpose</th></tr></thead><tbody><tr><td><a href="/pages/Aa6yuJ4hOsjF2VJ4FFHC"><code>line</code></a></td><td><code>plot.line(title, value, color, plottype?, histogramBase?, offset?, id?)</code></td><td>Continuous line</td></tr><tr><td><a href="/pages/mfG0MFKeYao6bRyyKVng"><code>filledArea</code></a></td><td><code>plot.filledArea(id, objATitle, objBTitle, title, color, transparency?, visible?, type?)</code></td><td>Shaded area</td></tr><tr><td><a href="/pages/gZqrtlCc7a2QcLHmPHPc"><code>colorer</code></a></td><td><code>plot.colorer(title, value, plotTargetTitle, colors, id?)</code></td><td>Discrete coloring of another series</td></tr><tr><td><a href="/pages/WqrCoNCnI7T4t1Tz2WOc"><code>barColorer</code></a></td><td><p><code>plot.barColorer(title, value,</code></p><p> <code>colors, id?)</code></p></td><td>Colors candles based on a series</td></tr><tr><td><a href="/pages/nhGSWQ821thI5yaNrARP">shapes</a></td><td><code>plot.shapes(title, value, text, color, textColor, plottype, location, size, offset?, transparency?, id?)</code></td><td>Adds markers like arrows, circles, or labels on the chart.</td></tr></tbody></table>
