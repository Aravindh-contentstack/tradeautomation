# time

Adds a **time input field** to your indicator’s Inputs panel. This is useful for session boundaries, alert times, or any setting where the user must specify a precise time (within a range).

### Syntax

`input.time(title, value, id?, min, max, tooltip?, group?, inline?)`

### Parameters

* **title** `string` — Label shown in the Inputs panel.
* **value** `number` — Default time value (numeric representation, typically seconds or timestamp depending on implementation).
* **id** `string` (optional) — Unique key for referencing this input. If omitted, it is auto-generated from `title` (lowercased, spaces removed).
* **min** `number` — The earliest allowed time value.
* **max** `number` — The latest allowed time value.
* **tooltip** `string` (optional) — Help text shown on hover.
* **group** `string` (optional) — Groups this input with others inside a collapsible section.
* **inline** `string` (optional) — Aligns multiple inputs in the same row if they share the same key.

### Return Value

`{ id: string }` — The assigned `id` for this time input.

### Example

```ts
//@version=1

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
  // Example: Trading session start time
  input.time(
    'Session Start',
    930,          // default: 9:30
    undefined,    // auto id: "sessionstart"
    0,            // min (0 = 00:00)
    2359,         // max (2359 = 23:59)
    'Time when trading session begins',
    'Sessions',
    'session-row'
  );

  // Example: Trading session end time
  input.time(
    'Session End',
    1600,         // default: 16:00
    undefined,
    0,
    2359,
    'Time when trading session ends',
    'Sessions',
    'session-row'
  );
}

// This is the main function. It is called every time a new candle is received.
// Here you can calculate the indicator values and draw the indicator on the chart.
onTick = (length, _moment, _, ta, inputs) => {

};
```

### Result

<figure><img src="/files/5sasaWoW2IwV2RJUqaRd" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

* Use consistent numeric formats (e.g., `930` for 9:30 AM, `1600` for 4:00 PM).
* Combine `time()` with `session()` for full session customization.
  {% endhint %}

{% hint style="danger" %}

### Warning

Always enforce valid ranges—if `min`/`max` are too wide, users may enter invalid or nonsensical times.
{% endhint %}

{% hint style="success" %}

### Good Practice

* **Good Practice:** Place start and end times on the same row using the same `inline` key (e.g., `"session-row"`) for a cleaner UI.
* **Good Practice:** Group all time-related inputs under a dedicated *Sessions* section for clarity.
  {% endhint %}
