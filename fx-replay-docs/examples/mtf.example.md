# Multi-Timeframe (MTF)

```typescript
//@version=1
// This is a template for a custom indicator. You can use this as a starting point to create your own custom indicator.


// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
    input.bool('show draws', true, 'show');
    input.bool('extend fvg', true, 'extend');
    let tm =input.timeframe("TimeFrame", "4h");
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
