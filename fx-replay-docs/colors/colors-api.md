# Colors

## Color API <a href="#color-api" id="color-api"></a>

> **File:** `color‑indicator.api.ts`

* Predefined colors: `color.red`, `color.green`, `color.blue`, `color.black`, `color.white`, etc.
* Utility: `rgba(r, g, b, a)` → returns `"rgba(...)"`.

The **Color API** provides a simple way to work with predefined base colors and to create custom RGBA colors for your indicators. It’s often used with the **Inputs API** (`input.color`) and other style options where colors are required.

***

### BaseColors

#### Description

A collection of predefined colors (hex values). These cover common colors like black, red, blue, yellow, etc.

#### Syntax

`BaseColors.<colorName>`

#### Available Colors

<table><thead><tr><th width="164">Name</th><th width="236">RGB Value</th></tr></thead><tbody><tr><td>black</td><td><code>rgb(0, 0, 0)</code></td></tr><tr><td>gray</td><td><code>rgb(128, 128, 128)</code></td></tr><tr><td>silver</td><td><code>rgb(192, 192, 192)</code></td></tr><tr><td>white</td><td><code>rgb(255, 255, 255)</code></td></tr><tr><td>maroon</td><td><code>rgb(128, 0, 0)</code></td></tr><tr><td>red</td><td><code>rgb(255, 0, 0)</code></td></tr><tr><td>purple</td><td><code>rgb(128, 0, 128)</code></td></tr><tr><td>fuchsia</td><td><code>rgb(255, 0, 255)</code></td></tr><tr><td>green</td><td><code>rgb(0, 128, 0)</code></td></tr><tr><td>lime</td><td><code>rgb(0, 255, 0)</code></td></tr><tr><td>olive</td><td><code>rgb(128, 128, 0)</code></td></tr><tr><td>yellow</td><td><code>rgb(255, 255, 0)</code></td></tr><tr><td>navy</td><td><code>rgb(0, 0, 128)</code></td></tr><tr><td>blue</td><td><code>rgb(0, 0, 255)</code></td></tr><tr><td>teal</td><td><code>rgb(0, 128, 128)</code></td></tr><tr><td>aqua</td><td><code>rgb(0, 255, 255)</code></td></tr></tbody></table>

#### Example

```ts
const redLine = color.red;
const background = color.black;
```

***

### RGBAColor

#### Description

Represents a color using **red, green, blue, and alpha (transparency)** components.

#### Syntax

```ts
type RGBAColor = {
  r: number; // 0–255
  g: number; // 0–255
  b: number; // 0–255
  a: number; // 0–1 (transparency)
};
```

#### Example

```ts
const semiTransparentBlue: RGBAColor = { r: 0, g: 0, b: 255, a: 0.5 };
```

***

{% hint style="info" %}

### Tips

* Use `color` when you want quick, predefined colors.
* Use `rgba()` when you need **transparency** or custom mixes.
  {% endhint %}

{% hint style="danger" %}

### Warning

Keep "rgba" values in valid ranges (`r,g,b` = 0–255, `a` = 0–1). Out-of-range values may cause errors or unexpected colors.
{% endhint %}

{% hint style="success" %}

### Good Practice

* Use strong, high-contrast defaults to ensure visibility on both light and dark chart themes.
* Group color inputs in a *Styles* section for a cleaner Inputs panel.
  {% endhint %}
