# Bollinger Bands

```javascript
//@version=1
init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
  input.int('Period', 20, 'period');
};

const closeArray = [];

const calculateSMA = (values, period) => {
  if (values.length < period) return null;
  const sum = values.slice(-period).reduce((a, b) => a + b, 0);
  return sum / period;
};

const calculateStdDev = (values, period, mean) => {
  if (values.length < period) return null;
  const slice = values.slice(-period);
  const variance = slice.reduce((acc, val) => acc + Math.pow(val - mean, 2), 0) / period;
  return Math.sqrt(variance);
};

onTick = (length, _moment, _, ta, inputs) => {
  const period = inputs.period;

  const close = closeC(0);
  closeArray.push(close);

  if (closeArray.length >= period) {
    const sma = calculateSMA(closeArray, period);
    const stdDev = calculateStdDev(closeArray, period, sma);

    const upperBand = sma + 2 * stdDev;
    const lowerBand = sma - 2 * stdDev;

    plot.line('SMA', sma, '#FFA500', 0);
    plot.line('Upper Band', upperBand, '#00FF00', 0);
    plot.line('Lower Band', lowerBand, '#FF0000', 0);
    plot.filledArea('band1', 'Upper Band', 'SMA', 'Band 1', '#0000FF', 60, true, "plot_plot");
    plot.filledArea('band2', 'SMA', 'Lower Band', 'Band 2', color.red, 60, true, "plot_plot")
  }
};

```
