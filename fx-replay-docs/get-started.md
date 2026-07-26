# START HERE

## 🚀 Hey! Start Here!

Welcome to **FXR Script**! 🎉\
First of all, THIS IS NOT PINESCRIPT. \
This is your new playground for building trading indicators and strategies.\
Don’t worry if you’ve never coded before — the structure is **super simple**, and you’ll pick it up quickly.

### Step-by-step: Create a Custom Indicator

#### 1) Open the Code Editor

1. Go to the **top-left** of the platform.
2. Click the **Editor** button.
3. The **Code Editor** will slide in from the **right side** of your screen.

<figure><img src="/files/6wdgSpLYpoeEnOeDyX9k" alt=""><figcaption></figcaption></figure>

#### 2) Create a new Custom Indicator

1. When the editor opens, you’ll land on a **Welcome** page with two options:
   * **New**: create a brand-new Custom Indicator
   * **Repository**: view and manage indicators you’ve already created
2. Click **New**.
3. A new window will appear—type a **name** for your indicator (pick something memorable).
4. Click to save—and that’s it. Your Custom Indicator is created and ready to edit.

<figure><img src="/files/Pf5oe6YQ3R2d4tLTNCy1" alt="" width="525"><figcaption></figcaption></figure>

#### 3) Run the template example

1. After creating it, you’ll see a **code template**. This is a quick example showing what you can build.
2. Want to see what the script does?
3. Click the **Run** button.
4. A confirmation window will pop up asking you to select/confirm the indicator—click **Proceed anyway**.
5. Boom ✅ The indicator will run and you’ll see it **plot shapes on the chart**.

<figure><img src="/files/7iXualhM3TT5a92hOqKM" alt=""><figcaption></figcaption></figure>

### How Scripts Work

Every script has **two main parts**:

1. **`init`** → runs once at the beginning (setup).
2. **`onTick`** → runs on every new candle or tick (your logic goes here).

That’s it! Just two places where you write your ideas.

### Basic Structure

Here’s the skeleton of every script:

```javascript
//@version=1
init = () => {
  // Setup your indicator
  indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = (length, _moment, _, ta, inputs) => {
  // Your trading logic goes here
};
```

* `indicator()` tells the platform what your script is and where to show it.
* Inside `onTick`, you can calculate values, draw lines, color bars, and more.

### Example 1: Adding an Input

Let’s create a **FVG indicator** that you can customize with an input box.

```javascript
//@version=1
// This is a template for a custom indicator. You can use this as a starting point to create your own custom indicator.

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
    input.bool('show draws', true, 'show');
    input.bool('extend fvg', true, 'extend');
    input.color('color FVG', color.green, 'colorfvg');
    input.color('color Background', color.rgba(0, 128, 0, 0.2), 'colorbg');
}

// This is the main function. It is called every time a new candle is received.
// Here you can calculate the indicator values and draw the indicator on the chart.
onTick = (length, _moment, _, ta, inputs) => {
    const show = inputs.show;
    const extend = inputs.extend;
    const colorFVG = inputs.colorfvg;
    const colorbg = inputs.colorbg;
    if (show) {
        if (high(2) < low(0) && (low(0) - high(2) > (closeC(1) - openC(1)) * 0.65)) {
            rectangle(time(2), high(2), time(0), low(0), { backgroundColor: colorbg, color: colorFVG, extendRight: extend });
        }
    }
}

```

### Example 2: Drawing Something Simple

Let’s draw a line connecting the **previous close** with the **current close**.

```javascript
//@version=1
init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = (length, _moment, _, ta, inputs) => {
  // Get current and previous closes
  const current = closeC(0);
  const previous = closeC(1);

  // Plot them as a line
  plot.line("Close Connection", current, color.orange);
};
```

Each candle will draw a line that follows the price movement.

> ### 💡 Tips to Get Started
>
> * Always start with `init` and `onTick` — keep it simple.
> * Use **inputs** to make your script flexible.
> * Use **plot functions** (`line`, `barColorer`, `filledArea`) to bring your ideas to life on the chart.
> * Don’t try to build something huge at first — start small (like moving averages or coloring bars).

***

✨ That’s it — you’re ready to start experimenting!\
Your first scripts might be super simple, and that’s perfect. Step by step, you’ll grow into more advanced logic.