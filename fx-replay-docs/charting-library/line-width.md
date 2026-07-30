# linewidth

The `linewidth` parameter controls how **thick** a line is drawn.\
It accepts a **number** (integer), typically in the range of **1 to 5**.

* **1 → Very thin line** (default).
* **2 → Thin but clearer line**.
* **3 → Medium thickness** (easy to see).
* **4 → Thick line** (emphasis).
* **5 → Very thick line** (highlighted / strong emphasis).

#### Example

```ts
// Thin dashed red line
band.line("Resistance", 2000, "#FF0000", 2, 1);

// Thick solid green line
band.line("Support", 1000, "#00FF00", 0, 4);
```

{% hint style="danger" %}

#### Warning

> Using a very high `linewidth` may clutter your chart, especially if you add multiple bands at close price levels.
> {% endhint %}

{% hint style="success" %}

#### Good Practice

> Stick to `linewidth` values between **1 and 3** for most use cases. Reserve **4 or 5** for very important levels (like key support/resistance).
> {% endhint %}

####
