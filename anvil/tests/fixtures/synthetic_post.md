---
title: "Synthetic Test Post — For Markdown Loader Self-Tests"
date: 2026-04-27
description: "A handcrafted markdown file used only by the test suite to exercise render.site.loaders.markdown without depending on the upstream blogs/ checkout."
tags: ["test", "fixture"]
author: "Test Suite"
showToc: true
draft: false
---

# This Heading Is Inside The Body

The frontmatter ends above; everything from here on is body markdown.

## A subsection with code

Inline `code()` and a fenced block:

```python
def hello() -> str:
    return "world"
```

## A subsection with a table

| col-A | col-B |
|-------|-------|
| 1     | 2     |
| 3     | 4     |

## Smarty curly quotes + em-dash

The smarty extension is enabled — meaning "this text" becomes typographically smart with curly quotes and an em-dash here.
