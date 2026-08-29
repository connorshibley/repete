#!/usr/bin/env python3
"""Extract a self-contained, theme-following SVG from an Archify artifact.

The artifact's <svg> carries only class names; every colour lives in CSS
variables defined elsewhere in the document ([data-theme="light"] for light,
:root for dark). Lifting the <svg> out on its own therefore renders it
unstyled, and lifting only the light palette leaves it bright on a dark page —
the exact trap repete3's TestSvgFollowsTheTheme was written about.

This pulls both palettes plus every rule the diagram uses, namespaces them
under .archify-map so they cannot collide with the host stylesheet, and drops
the interactive affordances the static embed cannot honour.

Usage:
  build_map_svg.py <artifact.html> <out.svg>              # style + svg in one
  build_map_svg.py <artifact.html> <out.svg> --css <out.css>   # split
"""
import re
import sys

LIGHT_SEL = '[data-theme="light"]'
DARK_SELS = (':root, [data-theme="dark"]',
             ':root, [data-theme="dark"], [data-theme="light"]')


def _strip_at_blocks(css: str) -> str:
    """Drop @media/@supports blocks so print and preset overrides cannot leak."""
    out, i = [], 0
    while i < len(css):
        if css[i] == '@':
            j = css.find('{', i)
            if j == -1:
                break
            depth, k = 1, j + 1
            while k < len(css) and depth:
                if css[k] == '{':
                    depth += 1
                elif css[k] == '}':
                    depth -= 1
                k += 1
            i = k
            continue
        out.append(css[i])
        i += 1
    return ''.join(out)


def _strip_controls(svg: str) -> str:
    """Remove affordances promising interaction this embed cannot deliver.

    repete2's test_the_dashboard_has_no_controls rejects these outright: a
    control on a page with no server behind it cannot act, but it can make an
    operator believe it did. role="img" on the root is a label and stays.
    """
    svg = re.sub(r'\s+tabindex="[^"]*"', '', svg)
    svg = re.sub(r'\s+role="button"', '', svg)
    svg = re.sub(r'\s+aria-(?:pressed|expanded|controls)="[^"]*"', '', svg)
    svg = re.sub(r'\s+on[a-z]+="[^"]*"', '', svg)
    return svg


def build(artifact: str) -> tuple[str, str]:
    """Return (css, svg_fragment)."""
    html = open(artifact, encoding='utf-8').read()
    svg = re.search(r'<svg\b.*?</svg>', html, re.S).group(0)
    css = re.search(r'<style[^>]*>(.*?)</style>', html, re.S).group(1)
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = _strip_at_blocks(css)

    used = set()
    for attr in re.findall(r'class="([^"]+)"', svg):
        used.update(attr.split())

    light, dark, rules = [], [], []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sel, body = ' '.join(m.group(1).split()), ' '.join(m.group(2).split())
        decls = re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', body)
        if sel == LIGHT_SEL:
            light.extend(decls)
            continue
        if sel in DARK_SELS:
            dark.extend(decls)
            continue
        parts = [p.strip() for p in sel.split(',')]
        keep = [p for p in parts
                if any(re.search(r'\.' + re.escape(c) + r'\b', p) for c in used)]
        if keep and body:
            rules.append((', '.join('.archify-map ' + k for k in keep), body))

    def block(pairs):
        return ''.join(f'{k}:{v.strip()};' for k, v in pairs)

    out = ['.archify-map{' + block(light) + '}']
    if dark:
        out.append('@media (prefers-color-scheme:dark){.archify-map{'
                   + block(dark) + '}}')
    out += [f'{sel}{{{body}}}' for sel, body in rules]

    svg = _strip_controls(svg)
    svg = svg.replace('<svg ', '<svg class="archify-map-svg" ', 1)
    return '\n'.join(out), svg


if __name__ == '__main__':
    art, out_svg = sys.argv[1], sys.argv[2]
    css, svg = build(art)
    if '--css' in sys.argv:
        css_path = sys.argv[sys.argv.index('--css') + 1]
        open(css_path, 'w', encoding='utf-8').write(css + '\n')
        open(out_svg, 'w', encoding='utf-8').write(
            f'<div class="archify-map">{svg}</div>\n')
        print(f"wrote {out_svg} and {css_path}")
    else:
        open(out_svg, 'w', encoding='utf-8').write(
            f'<div class="archify-map"><style>{css}</style>{svg}</div>\n')
        print(f"wrote {out_svg}")
