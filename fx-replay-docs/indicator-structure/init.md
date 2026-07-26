# init

## What is it?

The `Init` method is triggered a single time when your custom indicator is first loaded onto the chart.\
This is where you prepare everything the indicator needs before it starts running — such as setting its name, declaring variables, configuring inputs, and defining how results will be displayed.

## **Syntax**

```javascript
init = () => {
    // one-time setup logic here
}
```

## **What Happens in Init**

Think of `Init` as the **launch pad** for your strategy. Common tasks include:

* Assigning the strategy’s name and description.
* Creating variables and buffers to hold calculated values.
* Defining and registering user input parameters.
* Configuring visual settings like plots, colors, or line types.

## **Example**

```ts
//@version=1
// This is a template for a custom indicator. 
// You can use this as a starting point to create your own custom indicator.

// This is the setup function.
// Here you can define the input parameters for the indicator.
init = () => {
    input.bool('show draws', true, 'show');
    input.bool('extend fvg', true, 'extend');
}

```

## **Why It Matters**

Without `Init`, your strategy has no foundation — no parameters, no variables, no configuration.\
It’s the **setup phase** that ensures everything is ready before the first market tick arrives.

> 💡 **Tip:** Group related settings together in `Init` so they’re easier to find and adjust later. This makes your code more organized and saves time when making changes.

{% hint style="danger" %}
&#x20;**Warning**

> Don’t put trading logic or price calculations in `Init`. This method runs only once when the strategy loads, so any market checks here won’t update during execution.
> {% endhint %}

{% hint style="success" %}
**Good Practice**

> Keep `Init` focused on configuration — setting names, creating inputs, and initializing variables. This keeps your startup process clean and makes your strategy easier to debug later.
> {% endhint %}

***