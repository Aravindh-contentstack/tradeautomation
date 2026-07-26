# onTick

## **What is it?**

The `OnTick` method is the **heartbeat** of your custom indicator.\
It executes automatically every time the market updates (each tick) and processes your trading logic on the active chart.\
This is where you check market conditions, calculate values, and trigger buy/sell actions.

## **Syntax**

```ts
onTick = (length, _moment, _, ta, inputs) => {
    // per-tick or per-bar logic here
}
```

## **What Happens in "onTick"**

Think of `OnTick` as the **control center** that reacts to live market data. Common tasks include:

* Accessing price data for the current or previous bars.
* Calculating indicators or signals.
* Deciding whether to open, close, or hold a position.
* Ensuring actions run only once per bar to avoid duplicates.

## **Example**

```ts
// This is the main function. It is called every time a new candle is received.
// Here you can calculate the indicator values and draw the indicator on the chart.
onTick = (length, _moment, _, ta, inputs) => {
    const show = inputs.show;
    const extend = inputs.extend;
    if (show) {
        if(high(2) < low(0) && (low(0)-high(2) > (closeC(1)-openC(1)) * 0.65)){
        rectangle(time(2), high(2), time(0), low(0),{backgroundColor: color.rgba(0, 128, 0, 0.2), color: color.green, extendRight: extend});
        }
    }
};
```

## **Why It Matters**

Without `OnTick`, your indicator won’t react to the market.\
It’s where decisions happen in real time, turning your setup from `Init` into actual trades and indicator updates.

> 💡**Tip:**  Use `if(index % N === 0)` conditions inside `onTick` to control how often drawings are created, keeping charts clean and avoiding excessive markers.

{% hint style="danger" %}
&#x20;**Warning**

> Avoid placing heavy calculations or loops that run every tick unless absolutely necessary. They can slow down your strategy and cause performance issues, especially in fast-moving markets.
> {% endhint %}

{% hint style="success" %}
&#x20;**Good Practice**

> Store key values (like last signal direction or last drawn object ID) in variables during `onTick` so you can manage updates or prevent duplicate actions across bars.
> {% endhint %}

***