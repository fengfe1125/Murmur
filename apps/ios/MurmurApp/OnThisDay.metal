#include <metal_stdlib>
#include <SwiftUI/SwiftUI_Metal.h>
using namespace metal;

// 当年今日's send-off: the photo dissolves into its own particles.
//
// The fragment shader quantizes the layer into a grid; each cell gets a
// deterministic direction and delay from a hash of its index.  As progress
// advances, a cell's sample point lerps from the fragment itself (the intact
// image) to the cell's original centre, displaced along the cell's direction
// (a drifting particle carrying its patch's colour and brightness) — that is
// what keeps the photo legible all the way through the dissolve, instead of
// collapsing into uniform noise.
//
// Cell edge is the one aesthetic knob: under ~4pt the cells stop reading as
// particles and become noise; over ~14pt they read as mosaic redaction.
// The caller passes 8.

static float murmurHash21(float2 p) {
    p = fract(p * float2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

[[stitchable]] half4 onThisDayDissolve(
    float2 pos,
    SwiftUI::Layer layer,
    float cell,
    float progress,
    float maxDrift
) {
    float2 cellIndex = floor(pos / cell);
    float2 cellCenter = (cellIndex + 0.5) * cell;

    float delaySeed = murmurHash21(cellIndex);
    float angleSeed = murmurHash21(cellIndex + float2(71.7, 13.3));
    // Staggered starts, so the dissolve sweeps rather than switching off.
    float delay = delaySeed * 0.4;
    float t = clamp((progress - delay) / max(1.0 - delay, 1e-4), 0.0, 1.0);

    // Every particle leaves upward-ish: sideways scatter with an upward bias,
    // decelerating — it is being let go of, not thrown.
    float angle = angleSeed * 6.2831853;
    float2 direction = float2(cos(angle) * 0.45, -(0.55 + 0.45 * sin(angle)));
    float2 displacement = direction * (maxDrift * t * t);

    float2 samplePos = mix(pos, cellCenter - displacement, t);
    float fade = 1.0 - t;
    return layer.sample(samplePos) * half(fade * fade);
}
