# Trend Follower Indicator

```typescript
//@version=1
init = () => {
  indicator({ onMainPanel: false, format: 'inherit' });
  input.int('RSI Period', 21, 'rsiPeriod');
  input.int('SMA Period', 6, 'smaPeriod');

  band.line('Level 80', 80, '#787B86', 2, 1);
  band.line('Level 30', 30, '#787B86', 2, 1);
};

// Heikin Ashi series (persisten entre velas)
const haOpenSeries = [];
const haCloseSeries = [];
const haHighSeries = [];
const haLowSeries = [];
const haOhlc4Series = [];
const haOhlc4maSeries = [];
const haOhlc4maRsiSeries = [];

onTick = (length, _moment, _, ta, inputs) => {
  const rsiPeriod = inputs.rsiPeriod;
  const smaPeriod = inputs.smaPeriod;

  const open = openC(0);
  const highCandle = high(0);
  const lowCandle = low(0);
  const close = closeC(0);

  if ([open, highCandle, lowCandle, close].some(v => isNaN(v))) {
    return;
  }

  // Heikin Ashi Open
  const prevHaOpen = haOpenSeries.at(-1);
  const prevHaClose = haCloseSeries.at(-1);

  let haOpen;

  if (prevHaOpen !== undefined && prevHaClose !== undefined) {
    haOpen = (prevHaOpen + prevHaClose) / 2;
  } else {
    haOpen = (open + close) / 2;
  }

  if (isNaN(haOpen)) {
    return;
  }

  const haClose = (open + highCandle + lowCandle + close) / 4;
  const haHigh = Math.max(highCandle, haOpen, haClose);
  const haLow = Math.min(lowCandle, haOpen, haClose);

  const haOhlc4 = (haOpen + haHigh + haLow + haClose) / 4;

  if ([haClose, haHigh, haLow, haOhlc4].some(v => isNaN(v))) {
    return;
  }

  haOpenSeries.push(haOpen);
  haCloseSeries.push(haClose);
  haHighSeries.push(haHigh);
  haLowSeries.push(haLow);
  haOhlc4Series.push(haOhlc4);

  // SMA of Heikin Ashi OHLC4 (ta.sma returns an array)
  const smaArray = ta.sma(haOhlc4Series, smaPeriod);
  const haOhlc4SMA = smaArray.at(-1);

  if (isNaN(haOhlc4SMA)) {
    return;
  }

  haOhlc4maSeries.push(haOhlc4SMA);

  // RSI of SMA (ta.rsi returns an array)
  const rsiArray = ta.rsi(haOhlc4maSeries, rsiPeriod);
  const haOhlc4SmaRSI = rsiArray.at(-1);

  if (isNaN(haOhlc4SmaRSI)) {
    return;
  }

  haOhlc4maRsiSeries.push(haOhlc4SmaRSI);
  plot.line('Heikin Ashi SMA RSI', haOhlc4SmaRSI, '#FFFFFF', 0);

  let paletteIndex = 6; // default: white

  if (haOhlc4SmaRSI >= 90) {
    paletteIndex = 0;
  } else if (haOhlc4SmaRSI >= 80) {
    paletteIndex = 1;
  } else if (haOhlc4SmaRSI >= 70) {
    paletteIndex = 2;
  } else if (haOhlc4SmaRSI < 10) {
    paletteIndex = 3;
  } else if (haOhlc4SmaRSI < 20) {
    paletteIndex = 4;
  } else if (haOhlc4SmaRSI < 30) {
    paletteIndex = 5;
  }

  plot.colorer(
    'HA RSI Color',
    paletteIndex,
    'Heikin Ashi SMA RSI',
    [
      { name: '>=90', color: 'red' },
      { name: '>=80', color: 'orange' },
      { name: '>=70', color: 'yellow' },
      { name: '<10', color: 'green' },
      { name: '<20', color: 'blue' },
      { name: '<30', color: 'aqua' },
      { name: 'Other', color: 'white' },
    ]
  );
};

```
