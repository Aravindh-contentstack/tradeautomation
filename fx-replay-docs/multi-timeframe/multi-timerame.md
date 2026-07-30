# Multi-Timeframe (mtf) (BETA)

{% hint style="danger" %}

## Warning

#### It’s important to mention that this method is still in **BETA** and may present malfunctions or inconsistencies.

{% endhint %}

The **MTF (Multi-Timeframe)** API allows you to work with price data from a **different timeframe than the chart you are currently on**.\
This is extremely useful when you want to **combine higher-timeframe context** (like daily highs, 4H trends, or weekly indicators) with **lower-timeframe entries**.

MTF handles everything automatically for you. Once you request a timeframe, all `mtf.*` functions will transparently use that timeframe while normal price functions (`high()`, `closeC()`, etc.) will continue using the chart timeframe.

#### Syntax

`mtf.timeframe(timeframe)`

#### Parameters

timeframe `string`    — Defines the timeframe you want to request.\
Examples: `"1D"`, `"240"`, `"60"`, `"15"`, `"5"`

#### Return Value

`void` This method does not return a value. It prepares the requested timeframe so it can be used later inside `onTick`.

***

### MTF Functions

Once a timeframe is requested, you can access data from that timeframe using the following functions.

<details>

<summary>mtf.high</summary>

Returns the **high price** from the requested timeframe.

**Syntax**

`mtf.high(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.low</summary>

Returns the **low price** from the requested timeframe.

**Syntax**

`mtf.low(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.openC</summary>

Returns the **open price** from the requested timeframe.

**Syntax**

`mtf.openC(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.closeC</summary>

Returns the **close price** from the requested timeframe.

**Syntax**

`mtf.closeC(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.volume</summary>

Returns the **volume** from the requested timeframe.

**Syntax**

`mtf.volume(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.time</summary>

Returns the **timestamp** from the requested timeframe.

**Syntax**

`mtf.time(index, smooth)`

**Parameters**\
index `number` Candle index (0 = current MTF candle, 1 = previous, etc.)

smooth\
`boolean`

* `true` → smooth (interpolated, default)
* `false` → stepped (updates only when the MTF candle closes)

**Return Value** `number`

</details>

<details>

<summary>mtf.ema</summary>

Calculates an **EMA on the requested timeframe** and adapts it to the current chart.

**Syntax**

`mtf.ema(src, length, smooth)`

**Parameters**\
src\
`string`\
• `open`\
• `high`\
• `low`\
• `close`\
• `hl2`\
• `hlc3`\
• `ohlc4`

length `number` EMA period length

smooth `boolean` Controls interpolation mode

**Return Value** `number`

</details>

<details>

<summary>mtf.sma</summary>

Calculates an **SMA on the requested timeframe**.

**Syntax**

`mtf.sma(src, length, smooth)`

**Parameters**\
src\
`string`\
• `open`\
• `high`\
• `low`\
• `close`\
• `hl2`\
• `hlc3`\
• `ohlc4`

length `number` SMA period length

smooth `boolean` Controls interpolation mode

**Return Value** `number`

</details>

<details>

<summary>mtf.rsi</summary>

Calculates an **RSI on the requested timeframe**.

**Syntax**

`mtf.rsi(src, length, smooth)`

**Parameters**\
src\
`string`\
• `open`\
• `high`\
• `low`\
• `close`\
• `hl2`\
• `hlc3`\
• `ohlc4`

length `number` RSI period length

smooth `boolean` Controls interpolation mode

**Return Value** `number`

</details>

<details>

<summary>mtf.atr</summary>

Calculates **ATR from the requested timeframe**.

**Syntax**

`mtf.atr(length, smooth)`

**Return Value** `number`

</details>

#### Example

```typescript
//@version=1
// This is a template for a custom indicator. You can use this as a starting point to create your own custom indicator.

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
  input.bool('show draws', true, 'show');
  input.bool('extend fvg', true, 'extend');
  let tm = input.timeframe('TimeFrame', '4h');
  mtf.timeframe(tm);
};

// This is the main function. It is called every time a new candle is received.
// Here you can calculate the indicator values and draw the indicator on the chart.
onTick = (length, _moment, _, ta, inputs) => {
  const show = inputs.show;
  const extend = inputs.extend;

  if (show) {
    if (
      mtf.high(2) < mtf.low(0) &&
      mtf.low(0) - mtf.high(2) > (mtf.closeC(1) - mtf.openC(1)) * 0.65
    ) {
      rectangle(mtf.time(2), mtf.high(2), mtf.time(0), mtf.low(0), {
        backgroundColor: color.rgba(0, 128, 0, 0.2),
        color: color.green,
        extendRight: extend,
      });
    }
  }
};
```

#### Result

<figure><img src="/files/oEbrhB4GMhpJnJaii2sH" alt=""><figcaption></figcaption></figure>

{% hint style="info" %}

### Tips

Use higher timeframes (like 1D or 4H) to define trend direction and lower timeframes for entries.

Set `smooth = false` when you want values to update **only after the higher timeframe candle closes**.

Call `mtf.timeframe()` **only once** inside `init()`.
{% endhint %}

{% hint style="danger" %}

### Warning

Do not call `mtf.timeframe()` inside `onTick`.\
This can cause incorrect data synchronization and unexpected behavior.

Avoid mixing `mtf.*` values and normal price values without being aware of their different time origins.
{% endhint %}

{% hint style="success" %}

### Good Practice

Always comment which timeframe you are requesting to avoid confusion later.

Use MTF mainly for **context and filters**, not for ultra-precise entries.

Keep MTF calculations lightweight to maintain performance on lower timeframes.
{% endhint %}
