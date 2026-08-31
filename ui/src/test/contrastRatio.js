/**
 * contrastRatio.js — WCAG 2.x sRGB relative-luminance contrast ratio,
 * computed by hand (no new dependency — per the repair task's constraint,
 * prefer computing contrast ourselves from resolved colors).
 */

/** Parses `#rgb`, `#rrggbb`, `rgb(r,g,b)` or `rgba(r,g,b,a)` into [r,g,b] (0-255). Throws on anything else. */
export function parseColor(value) {
  const v = String(value).trim()
  const hex3 = v.match(/^#([0-9a-fA-F]{3})$/)
  if (hex3) {
    const [r, g, b] = hex3[1].split('').map((c) => parseInt(c + c, 16))
    return [r, g, b]
  }
  const hex6 = v.match(/^#([0-9a-fA-F]{6})$/)
  if (hex6) {
    const h = hex6[1]
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
  }
  const rgb = v.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/)
  if (rgb) {
    const alpha = rgb[4] == null ? 1 : parseFloat(rgb[4])
    if (alpha < 0.99) {
      throw new Error(`contrastRatio: cannot score a translucent color without compositing: "${v}"`)
    }
    return [parseFloat(rgb[1]), parseFloat(rgb[2]), parseFloat(rgb[3])]
  }
  throw new Error(`contrastRatio: unparseable color "${v}"`)
}

function relativeLuminance([r, g, b]) {
  const chan = (c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  const [R, G, B] = [chan(r), chan(g), chan(b)]
  return 0.2126 * R + 0.7152 * G + 0.0722 * B
}

/** WCAG contrast ratio between two colors (any parseable format), 1:1 .. 21:1. */
export function contrastRatio(colorA, colorB) {
  const La = relativeLuminance(parseColor(colorA))
  const Lb = relativeLuminance(parseColor(colorB))
  const lighter = Math.max(La, Lb)
  const darker = Math.min(La, Lb)
  return (lighter + 0.05) / (darker + 0.05)
}

/** WCAG 1.4.3 AA floor: 4.5:1 for normal text, 3:1 for large text (>=24px, or >=19px bold). */
export function aaFloor({ largeText = false } = {}) {
  return largeText ? 3 : 4.5
}
