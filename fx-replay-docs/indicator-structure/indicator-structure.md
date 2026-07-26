# Indicators Structure

## 🗝 Key Building Blocks

### Indicator Lifecycle

* [**init** ](/custom-indicators-docs/indicators-structure/init.md)— Runs once when the indicator loads. Sets up user inputs, visual settings, and where it will be displayed (main chart or separate panel).
* [**onTick** ](/custom-indicators-docs/indicators-structure/ontick.md)— Runs each price update. Calculates indicator values and updates the chart visuals.

> 💡 **Tip:** Keep your `init` function simple. Only set up what’s necessary at startup — this makes your indicator load faster and keeps it easier to maintain.

***