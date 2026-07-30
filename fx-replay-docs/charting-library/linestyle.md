# linestyle

In the **Charting Library**, the `linestyle` parameter is always a **number enum** that maps to different line styles.

### 🎨 LineStyle Options

* **0 → Solid line**
* **1 → Dotted line**
* **2 → Dashed line**
* **3 → Large dashed line** (long dashes, spaced out)
* **4 → Sparse dotted line** (widely spaced dots)

{% hint style="danger" %}

#### Warning

> Using an unsupported number for `linestyle` will either **default to solid** or cause your line not to display.
> {% endhint %}

{% hint style="success" %}

#### Good Practice

> Always document which `linestyle` you used in your code (e.g., “2 = dashed”). This makes it easier for others — and your future self — to understand the visual style at a glance.
> {% endhint %}

####
