# int

Adds an **integer number field** to your indicator’s Inputs panel. Perfect for whole-number settings like “Period”, “Length”, or “Bars to look back”. You can define minimum, maximum, and step values to restrict user input.

### Syntax

`input.int(title, value, id?, min?, max?, step?, tooltip?, group?, inline?)`

### Parameters

* **title** `string` — Label shown in the Inputs panel.
* **value** `number` — Default integer value.
* **id** `string` (optional) — Unique key for referencing this input. Auto-generated from `title` if omitted (lowercased, spaces removed).
* **min** `number` (optional) — Minimum allowed value.
* **max** `number` (optional) — Maximum allowed value.
* **step** `number` (optional) — Increment between values (e.g., `5` → values like 5, 10, 15).
* **tooltip** `string` (optional) — Hover text for UI guidance.
* **group** `string` (optional) — Groups related inputs into collapsible sections.
* **inline** `string` (optional) — Aligns multiple inputs in the same row if they share the same `inline` key.

### Return Value

`{ id: string }` — The assigned `id`, either custom or auto-generated.

### Example

```ts
//@version=1

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
  // Example: Add an integer input for SMA length
  input.int(
    'SMA Length',
    14,
    undefined,       // auto id: "smalength"
    1,               // min
    200,             // max
    1,               // step
    'Number of bars used to calculate SMA',
    'Moving Averages',
    'ma-row'
  );

  // Later in your code:
  // inputs[smaLength.id] → gives integer chosen by user
}

onTick = (length, _moment, _, ta, inputs) => {
  
};
```

### Result

<figure><img src="/files/KMLvbfgNMmUWTdAjrmnC" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Use `min` and `max` to prevent invalid values (e.g., period length must be >0).
* Adding a `step` makes the spinner more user-friendly (e.g., jump in multiples of 5).
  {% endhint %}

{% hint style="danger" %}

### Warning

If you don’t constrain `min`/`max`, users can enter any number.  Always validate in your logic if limits are important.
{% endhint %}

{% hint style="success" %}

### Good Practice

* Use descriptive titles (“SMA Length” instead of “Length”) so the setting is clear even when grouped.
* Group all numeric parameters for one indicator under the same `group` for better organization  \
  (e.g., *Moving Averages*).
  {% endhint %}
