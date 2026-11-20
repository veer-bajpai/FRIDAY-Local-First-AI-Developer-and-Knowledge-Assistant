# Thinking indicator

In a non-React interface, reuse `thinking.css`, record `performance.now()` when the request starts, and update the timer with `requestAnimationFrame` (or a 250 ms interval). On completion, error, or cancellation, clear the timer and render `Thought for Ns` using the frozen elapsed value.

