# Calc 101 — Sample Notes (smoke-test fixture)

This is a tiny file you can drop into `bart/materials/` to verify bart is wired up end-to-end **without** burning real API tokens. Use with `./run run --dry-run`.

---

## Chapter 1 — Limits

A function $f$ has limit $L$ at $a$ if for every $\varepsilon > 0$ there exists $\delta > 0$ such that
$$0 < |x - a| < \delta \;\Longrightarrow\; |f(x) - L| < \varepsilon.$$

Key examples:
- $\lim_{x \to 0} \frac{\sin x}{x} = 1$
- $\lim_{x \to \infty} \left(1 + \frac{1}{x}\right)^x = e$

## Chapter 2 — Derivatives

The derivative of $f$ at $a$:
$$f'(a) = \lim_{h \to 0} \frac{f(a+h) - f(a)}{h}.$$

Standard rules: power rule, product rule, quotient rule, chain rule.

## Chapter 3 — Integrals

The fundamental theorem of calculus:
$$\int_a^b f'(x)\,dx = f(b) - f(a).$$

## Practice problem

Compute $\int_0^\pi \sin x \,dx$.

Answer: $-\cos(\pi) + \cos(0) = 1 + 1 = 2$.
